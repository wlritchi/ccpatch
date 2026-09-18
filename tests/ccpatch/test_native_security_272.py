"""Opt-in behavior checks for complete captured 2.1.272 security functions."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import default_patch_sets

ROOT = Path(__file__).resolve().parents[2]


def _between(source: str, start: str, end: str) -> str:
    assert source.count(start) == 1, start
    position = source.index(start)
    stop = source.index(end, position + len(start))
    result = source[position:stop]
    assert result.endswith("}")
    return result


def _run(tmp_path: Path, source: str, assertions: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    program = tmp_path / "native-security.mjs"
    program.write_text(
        'import assert from "node:assert/strict";\n' + source + "\n" + assertions
    )
    result = subprocess.run(  # noqa: S603 - Execute captured functions with local stubs.
        [node, str(program)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_complete_extraction_retains_nested_declarations(tmp_path: Path) -> None:
    source = (
        'function outer(){function inner(){return "}"}return inner()}'
        'function boundary(){}'
    )
    function = _between(source, "function outer(){", "function boundary()")
    _run(tmp_path, function, 'assert.equal(outer(),"}");')


@pytest.fixture(scope="module")
def sources() -> tuple[str, str]:
    if os.environ.get("CCPATCH_NATIVE_SECURITY") != "1":
        pytest.skip("set CCPATCH_NATIVE_SECURITY=1 for captured .272 checks")
    path = ROOT / "build/sweep-resume/2.1.272/linux-x64/original.js"
    assert path.is_file(), "pristine .272 capture is required"
    original = path.read_text()
    assert 'VERSION:"2.1.272"' in original
    patched = original
    for patch_set in default_patch_sets((2, 1, 272)):
        patched = patch_set.apply(patched)
    return original, patched


def _unchanged(sources: tuple[str, str], start: str, end: str) -> str:
    original, patched = (_between(source, start, end) for source in sources)
    assert original == patched
    return patched


def test_scoped_remote_control_veto(sources: tuple[str, str], tmp_path: Path) -> None:
    function = _unchanged(
        sources, 'function kun(){let e=import.meta.require(', "var Tfe="
    )
    module_path = re.search(r'import.meta.require\(("[^"]+")\)', function)
    assert module_path is not None
    _run(
        tmp_path,
        """
let project, local, alias=false, trusted, legacy;
const t=()=>{}, se=()=>({remoteControlAtStartup:legacy});
const Tfe={policySettings:"policy",flagSettings:"flag",userSettings:"user"};
import.meta.require=path=>{
 assert.equal(path,PATH);
 return {projectSettingsAliasesUserSettings:()=>alias,
 getSettingsForSource:s=>({remoteControlAtStartup:s==="projectSettings"?project:local}),
 getSecuritySensitiveSettingWithSources:()=>trusted===undefined?[]:[trusted]};
};
""".replace("PATH", module_path[1])
        + function,
        """
for(const source of Object.keys(Tfe)) {
 trusted={value:true,source};
 for(const pair of [[false,undefined],[undefined,false],[false,true],[true,false]]) {
  [project,local]=pair;
  assert.deepEqual(kun(),{value:false,source:"project_or_local_false"});
 }
 project=local=undefined;
 assert.deepEqual(kun(),{value:true,source:Tfe[source]});
}
trusted=undefined; legacy=undefined;
for(const pair of [[true,undefined],[undefined,true],[true,true]]) {
 [project,local]=pair; assert.deepEqual(kun(),{value:undefined,source:"none"});
}
project=false; local=undefined; alias=true; legacy=true;
assert.deepEqual(kun(),{value:true,source:"legacy_global_config"});
local=false; assert.equal(kun().value,false);
""",
    )


def test_organization_pin_fail_closed(sources: tuple[str, str], tmp_path: Path) -> None:
    function = _unchanged(sources, "async function jP(e){", "function j7()")
    _run(
        tmp_path,
        """
let policy={}, firstParty=true, unreadable=false, errors=[], token={accessToken:"token"},
 organization={organization_uuid:"allowed"}, credentialSource="claude.ai", nonOAuth=false;
const a={}, me=()=>policy, jl=()=>firstParty, bK=()=>unreadable, $fe=()=>errors,
 bme=()=>nonOAuth, gEe=()=>false, Upe=()=>false, Us=async()=>{}, Qt=()=>token,
 ac=()=>({source:credentialSource}), u3e=async()=>organization,
 g=()=>{}, y=()=>{}, u=x=>x, Ie=()=>"firstParty", fn=async()=>{},
 he=x=>x, _=x=>x, BG=["EACCES"], yme=/EACCES/;
"""
        + function,
        """
assert.equal((await jP({})).valid,true);
policy={forceLoginOrgUUID:[]}; assert.equal((await jP({})).valid,false);
policy={forceLoginOrgUUID:"allowed"}; assert.equal((await jP({})).valid,true);
policy={forceLoginOrgUUID:["other","allowed"]}; assert.equal((await jP({})).valid,true);
organization=null; assert.equal((await jP({})).valid,false);
organization={organization_uuid:"wrong"};
for(const source of ["claude.ai","CLAUDE_CODE_OAUTH_TOKEN","CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR"]) {
 credentialSource=source; const result=await jP({});
 assert.equal(result.valid,false); assert.match(result.message,/wrong/);
}
token=null; assert.equal((await jP({})).valid,true);
unreadable=true; errors=[{errorClass:"unreadable",message:"EACCES",file:"managed.json"}];
assert.equal((await jP({})).valid,false);
unreadable=false; firstParty=false; nonOAuth=true;
assert.equal((await jP({})).valid,false);
a.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST="1";
assert.equal((await jP({})).valid,true);
delete a.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST;
a.ANTHROPIC_UNIX_SOCKET="host.sock"; assert.equal((await jP({})).valid,true);
""",
    )


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_generic_auth_descriptor(
    sources: tuple[str, str], tmp_path: Path, asynchronous: bool
) -> None:
    sync = _unchanged(sources, "function D({envVar:e,wellKnownPath:", "function S(e,n)")
    async_function = _unchanged(
        sources, "async function F({envVar:e,wellKnownPath:", "function kUt()"
    )
    _run(
        tmp_path,
        """
let cached, fallback=null, descriptor=" secret ", direct="direct", inherited=false,
 failure=null, writes=[], reads=0, invalidated=[];
const a={}, t=()=>{}, l=String, k=e=>e.code, Roe=4096,
 w=()=>fallback, I=async()=>fallback, Kpn=()=>inherited,
 S=()=>{reads++;return direct}, B2t=e=>invalidated.push(e),
 Wke=()=>{reads++;if(failure)throw {code:failure};return descriptor},
 h=(...args)=>writes.push(args), v=async(...args)=>writes.push(args);
const options={envVar:"TEST_AUTH_FD",wellKnownPath:"credential",label:"token",
 getCached:()=>cached,setCached:v=>cached=v,credentials:{},skipInReviewOrigin:true};
"""
        + sync
        + async_function
        + f"\nconst read=()=>{'F' if asynchronous else 'D'}(options);",
        """
for(const key of ["CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR","CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
 "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR"]) {
 options.envVar=key;
 cached=undefined; process.env[key]="bad"; reads=0;
 assert.equal(await read(),null); assert.equal(reads,0);
 cached=undefined; delete process.env[key]; fallback="fallback";
 assert.equal(await read(),"fallback");
 cached=undefined; process.env[key]="7"; fallback=null; writes=[];
 assert.equal(await read(),"secret"); assert.equal(writes.length,1);
 reads=0; assert.equal(await read(),"secret"); assert.equal(reads,0);
 cached=undefined; descriptor=" "; writes=[];
 assert.equal(await read(),null); assert.equal(writes.length,0); descriptor=" secret ";
 cached=undefined; inherited=true; invalidated=[];
 assert.equal(await read(),"direct"); assert.equal(process.env[key],undefined);
 assert.deepEqual(invalidated,[key]);
 cached=undefined; process.env[key]="7"; a.CLAUDE_CODE_REMOTE="1"; fallback="remote"; reads=0;
 assert.equal(await read(),"remote"); assert.equal(reads,0); assert.equal(process.env[key],"7");
 delete a.CLAUDE_CODE_REMOTE; inherited=false; fallback=null;
 for(const code of ["EACCES","EPERM","ENOENT"]) {
  cached=undefined; process.env[key]="7"; failure=code;
  assert.equal(await read(),code==="ENOENT"?null:"direct");
 }
 failure=null; delete process.env[key];
}
""",
    )


@pytest.mark.parametrize("transformed", [False, True], ids=["native", "patched"])
def test_companion_scrub_native_builder(
    sources: tuple[str, str], tmp_path: Path, transformed: bool
) -> None:
    original, patched = (
        _between(source, "function Xe(e,r,s,o,n)", "function qe(e,r)")
        if "function Xe(e,r,s,o,n)" in source
        else _between(source, "function Xe(e,r,s,o,n,", "function qe(e,r)")
        for source in sources
    )
    scrub_start = "else{let g=new Set,w=new Set,E=new Set"
    scrub_end = "if(n)d.CLAUDE_BG_RV_AUTH"
    native_scrub = original[original.index(scrub_start) : original.index(scrub_end)]
    assert native_scrub in patched
    registry = re.search(r'pke=(\[\{endpoint:[\s\S]*?\}\]),fmt=', sources[0])
    skips = re.search(r'kBt=(\[[^\]]+\])', sources[0])
    assert registry is not None and skips is not None
    companion = _unchanged(
        sources, "function re(e,r){return!!e[r.endpoint]", "function ye(e)"
    )
    # Isolate the native scrub from requester snapshot restoration.
    _run(
        tmp_path,
        'const pke='
        + registry[1]
        + ',kBt='
        + skips[1]
        + ";"
        + """
const O=()=>"linux",N=()=>false,I=()=>false,te=()=>{},z9=()=>{},D1e=()=>{},E4=()=>{}, // codespell:ignore te
 N6e=()=>false,rge=()=>false,He=x=>x==="1"||x==="true",OCe=[],pe=[],RBt=[],fmt=[],oW=[]; // codespell:ignore oce
globalThis.__ccpatchRuntime={provider:{_ccProviderRetain:()=>({}),_ccProviderKeys:()=>[]}};
"""
        + companion
        + (patched if transformed else original),
        """
const saved=process.env;
try {
 for(const route of pke) {
  const child={[route.endpoint]:"https://same",ANTHROPIC_AUTH_TOKEN:"secret",
   ANTHROPIC_CUSTOM_HEADERS:"Authorization: secret"};
  if(route.selection)child[route.selection]="1";
  for(const key of route.companions)if(kBt.includes(key))child[key]="1";
  process.env={...child};
  const job={env:{...child},launch:{mode:"claude"},short:"test"};
  let result=Xe(job,"dir",null,"socket",null);
  assert.equal(result.ANTHROPIC_CUSTOM_HEADERS,child.ANTHROPIC_CUSTOM_HEADERS);
  job.env[route.endpoint]="https://changed";
  result=Xe(job,"dir",null,"socket",null);
  assert.equal(result[route.endpoint],undefined);
  assert.equal(result.ANTHROPIC_CUSTOM_HEADERS,undefined);
  assert.equal(result.ANTHROPIC_AUTH_TOKEN,undefined);
  for(const key of route.companions)assert.equal(result[key],undefined);
 }
} finally {process.env=saved}
""",
    )
