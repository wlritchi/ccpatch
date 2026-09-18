"""Check attach-stall snapshots against unmodified native function captures."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.bunfmt import parse_blob
from ccpatch.container import load_container
from ccpatch.patches import (
    BACKGROUND_PROVIDER_ENV_198,
    default_patch_sets,
)

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = (179, 182, 197, 198, 211, 272)
STALL = next(
    patch
    for patch in BACKGROUND_PROVIDER_ENV_198.patches
    if patch.name == "preserve-provider-on-attach-stall-respawn"
)
DEPENDENCIES = {
    179: "w4 P1 u6H",
    182: "fa Tc yoe",
    197: "Yi mc ile",
    198: "Zi dc fle prm",  # codespell:ignore fle
    211: "Ha dc MRe JN_",
    272: "fr mr A8 Sn",
}


def _source(version: int) -> str:
    path = ROOT / f"build/sweep-resume/2.1.{version}/linux-x64/original.js"
    if path.is_file():
        return path.read_text()
    binary = ROOT / "build/sweep-resume/long-full-suite/179/package/claude"
    if version == 179 and binary.is_file():
        blob = parse_blob(load_container(binary.read_bytes()).read_blob())
        return next(
            module.contents.decode()
            for module in blob.modules
            if module.is_entrypoint()
        )
    pytest.skip(f"requires pristine .{version} capture")


@pytest.mark.parametrize("version", VERSIONS)
def test_all_patch_sets_accept_stall_capture(version: int) -> None:
    source = _source(version)
    for patch_set in default_patch_sets((2, 1, version)):
        source = patch_set.apply(source)


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("blocked", (False, True))
def test_native_stall_retains_snapshot(version: int, blocked: bool) -> None:
    runtime = shutil.which("node")
    if runtime is None:
        pytest.skip("requires node")
    source = _source(version)
    matches = list(STALL.pattern.finditer(source))
    assert len(matches) == 1
    match = matches[0]
    name = re.match(r"function ([\w$]+)", match[0])
    assert name is not None
    end = re.search(r"\}(?=(?:async )?function |var )", source[match.end() :])
    assert end is not None
    function = source[match.start() : match.end() + end.end()]
    if source[max(0, match.start() - 6) : match.start()] == "async ":
        function = "async " + function
    assert not isinstance(STALL.replacement, str)
    patched = function.replace(match[0], STALL.replacement(match), 1)
    assert f".dispatch{match['separator']}" in patched
    dependencies = DEPENDENCIES[version].split()
    script = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const [source, name, dependencies, blocked] = INPUT;
const snapshot = Object.freeze({ANTHROPIC_API_KEY:"synthetic-requester"});
const replacement = Object.freeze({ANTHROPIC_API_KEY:"synthetic-daemon"});
const calls = [], errors = [];
let reads = 0, kills = 0;
const worker = {
    providerEnv:snapshot,
    _ccProviderBlocked:()=>blocked,
    dispatch:{short:"test",sessionId:"session",cwd:"/inert",launch:{mode:"prompt"}},
    record:{outcome:true},
    getPhase:()=>({kind:"running"}),
    kill:()=>{kills++;worker.providerEnv=replacement;},
};
const context = {
    [dependencies[0]]:async()=>{reads++;worker.providerEnv=replacement;return null;},
    [dependencies[1]]:value=>value,
    [dependencies[2]]:async()=>({hasMessages:true,path:"/inert/transcript"}),
    ...(dependencies[3] ? {[dependencies[3]]:0} : {}),
    u_:value=>value,G1:value=>value,
    Yes:value=>value,Q6r:value=>value,Ust:value=>value,E$n:value=>value,
    De:error=>errors.push(error),Re:error=>errors.push(error),
    Le:error=>errors.push(error),xe:error=>errors.push(error),m:error=>errors.push(error),
};
vm.createContext(context);
vm.runInContext(source,context,{timeout:1000});
(async()=>{
    await context[name](worker,{destroyed:false},async(...args)=>calls.push(args),
        ()=>false,new Set(),{});
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(errors,[]);
    if(blocked){
        assert.equal(reads,0);assert.equal(kills,0);assert.equal(calls.length,0);
    }else{
        assert.equal(kills,1);assert.equal(calls.length,1);
        assert.equal(calls[0].at(-1),snapshot);
        assert.equal(calls[0][0].source,"respawn");
        assert.equal(calls[0][0].attachStallRespawns,1);
        assert.equal(calls[0][0].launch.mode,"resume");
        assert.equal(calls[0][1],0);assert.equal(calls[0][2],undefined);
        assert.equal(calls[0].length,dependencies[0]==="fr"?5:4);
        if(calls[0].length===5)assert.equal(calls[0][3],false);
        assert.equal(worker.providerEnv,replacement);
    }
})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace("INPUT", json.dumps([patched, name[1], dependencies, blocked]))
    result = subprocess.run(  # noqa: S603 - Run captured code with inert dependencies.
        [runtime, "-e", script], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
