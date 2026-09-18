"""Run native agents configuration and handoff fragments without live services."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.agents_handoff import (
    _GETTER,
    _HANDOFF,
    _PARSER,
    _SERIALIZER,
    _handoff,
    agents_view_handoff,
)
from ccpatch.module_runtime import finalize_module_runtime, source_modules
from ccpatch.patches import DEV_CHANNEL_INHERITANCE, PatchError

_ROOT = Path(__file__).resolve().parents[2] / "build/sweep-resume"


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    end = source.index("function ", start + 9)
    return source[start:end].split("var ", 1)[0]


@pytest.mark.parametrize("version", ["2.1.182", "2.1.193", "2.1.195", "2.1.203"])
def test_native_agents_handoff(version: str) -> None:
    path = _ROOT / version / "linux-x64/original.js"
    if not path.exists():
        pytest.skip("native capture unavailable")
    original = path.read_text()
    version_tuple = tuple(map(int, version.split(".")))
    source = DEV_CHANNEL_INHERITANCE.apply(original)
    patched = agents_view_handoff(version_tuple).apply(source)
    match = _HANDOFF.search(source)
    assert match is not None
    getter = _GETTER.search(source)
    parser = _PARSER.search(source)
    serializer = _SERIALIZER.search(source)
    assert getter and parser and serializer
    body = _handoff(match)
    gate = re.match(r"[\w$]+", body)
    assert gate is not None
    fallback = re.search(r'return ([\w$]+)\(\{args:', body)
    logger = re.search(r'catch\([\w$]+\)\{([\w$]+)\(', body)
    accessibility = re.search(r'\.\.\.([\w$]+)\(\)\}\}\)$', body)
    assert fallback and logger and accessibility
    defaults_serializer = re.search(r'\.\.\.([\w$]+)\([\w$]+\)', match.group("middle"))
    functions = (
        _function(source, parser["parser"])
        + _function(source, serializer["serializer"])
        + _function(patched, "_ccAgentsDispatchArgs")
    )
    registration_patch = DEV_CHANNEL_INHERITANCE.patches[-1]
    registration = registration_patch.pattern.search(original)
    assert registration is not None
    block = registration_patch.pattern.sub(
        registration_patch.replacement, registration[0]
    )
    block = block[block.index('if(process.env.CLAUDE_CODE_SESSION_KIND') :]
    functions += (
        f'function registerFromWorker({registration[5]}){{let {registration[2]}=[];'
        f'function {registration[3]}(specs){{return specs.map(name=>({{name}}))}}'
        f'let entries=[];function {registration[4]}(specs){{entries=specs}}'
        + block
        + ';return entries}'
    )
    declarations = (
        f'let saved=[];function {getter["getter"]}(){{return saved}};'
        f'let enabled=true;function {gate[0]}(){{return enabled}};'
        f'async function {match["mount"]}(job,load,options){{return options}};'
        f'function {fallback[1]}(options){{return options}};'
        f'function {logger[1]}(){{}};function {accessibility[1]}(){{return {{}}}};'
    )
    if defaults_serializer:
        declarations += f'function {defaults_serializer[1]}(){{return []}};'
    defaults = match["defaults"]
    arguments = [match["job"], match["load"]] + ([defaults] if defaults else [])
    script = (
        functions
        + declarations
        + (
            f'async function launch({",".join(arguments)}){{if({body}}}'
            + '''
(async()=>{
 const assert=require("node:assert/strict");
 for(const flagged of [false,true]){
  process.argv=["node","claude",...(flagged?["--dangerously-load-development-channels","plugin:notifications"]:[]),"--channels","plugin:approved"];
  saved=["--settings","/normalized/settings.json","--plugin-dir","/normalized/plugins",
    "--add-dir","/normalized/project","--strict-mcp-config","--channels","plugin:approved",
    "--allow-dangerously-skip-permissions","--disable-slash-commands","--fallback-model","secret"];
  for(const inProcess of [false,true]){
   enabled=inProcess;
   const result=await launch("job",null,{});
   const args=inProcess?result.dispatchExtraArgs:result.args.slice(1);
   assert.deepEqual(args,["--settings","/normalized/settings.json","--plugin-dir","/normalized/plugins",
    "--add-dir","/normalized/project","--strict-mcp-config",
    ...(flagged?["--dangerously-load-development-channels","plugin:notifications"]:[]),
    "--channels","plugin:approved"]);
   const index=args.indexOf("--dangerously-load-development-channels");
   process.env.CLAUDE_CODE_SESSION_KIND="bg";
   process.env.CLAUDE_DEV_CHANNELS="plugin:stale";
   assert.deepEqual(registerFromWorker(index<0?[]:[args[index+1]]),
     flagged?[{name:"plugin:notifications",dev:true}]:[]);
   process.env.CLAUDE_CODE_SESSION_KIND="interactive";
   assert.deepEqual(registerFromWorker(["plugin:notifications"]),[]);
  }
 }
 saved=["--channels","plugin:stale","--dangerously-load-development-channels","plugin:stale"];process.argv=["node","claude"];
 enabled=true;assert.deepEqual((await launch("job",null,{})).dispatchExtraArgs,[]);
 enabled=false;assert.deepEqual((await launch("job",null,{})).args,["agents"]);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        )
    )
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    result = subprocess.run(  # noqa: S603
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_split_handoff_uses_module_local_native_functions() -> None:
    bodies = {
        "getter.js": 'function getter(){return state.replConfigArgv}',
        "config.js": 'function parser(args){let flag=!1,value,config={addDir:[],pluginDir:[],};return {config}}function serializer(config){return[...config.settings?["--settings",config.settings]:[]]}',
        "launch.js": 'if(gate("tengu_bg_leftarrow_inprocess",!0))try{return await mount(job,load,{dispatchDefaults:defaults})}catch(error){log(error)}return spawn({args:["agents",...serialize(defaults)],env:{CLAUDE_AGENTS_SELECT:job,...environment()}})',
    }
    source = ''.join(
        f'\n/* ccpatch-module:{name.encode().hex()} */\n{body}'
        for name, body in bodies.items()
    )
    patchset = agents_view_handoff((2, 1, 195))
    source = source.replace(
        'function serializer',
        'const channels=[0,...(entry.channels??[]).flatMap((item)=>["--channels",item])];function serializer',
    )
    patched = patchset.apply(source)
    modules = source_modules(finalize_module_runtime(patched))
    assert len(modules) == 3
    for module in modules:
        assert module.source.startswith('globalThis.__ccpatchRuntime??=')
    assert 'agentsHandoff.getter=getter;' in modules[0].source
    assert 'agentsHandoff.parser=parser;' in modules[1].source
    assert 'agentsHandoff.serializer=serializer;' in modules[1].source
    assert 'agentsHandoff.dispatchArgs()' in modules[2].source
    assert 'agentsHandoff.parser=parser;' not in modules[2].source


def test_handoff_rejects_unknown_source() -> None:
    with pytest.raises(PatchError):
        agents_view_handoff((2, 1, 203)).apply("unknown")


@pytest.mark.parametrize(
    "expression",
    [
        "state.replConfigArgv",
        "state.replConfigArgv()",
        "state.host.launchOptions.replConfigArgv()",
        "state().host.launchOptions.replConfigArgv()",
    ],
)
def test_launch_option_getter_shapes(expression: str) -> None:
    assert _GETTER.fullmatch(f"function getter(){{return {expression}}}") is not None


def test_restricted_handoff_preserves_native_security() -> None:
    source = 'gate("tengu_bg_leftarrow_inprocess",!0))try{return await mount(job,load,{...restricted()&&{dispatchExtraArgs:["--restricted"]},dispatchDefaults:defaults,...selection?.autoOpenJobId!==void 0&&{autoOpenJobId:selection.autoOpenJobId},originSpawn:origin,storageV5:storage,credentials:credentials,fleetNudgeStore:selection?.fleetNudgeStore})}catch(error){log(error)}let result=await spawn({args:["agents",...serialize(defaults)],env:{CLAUDE_AGENTS_SELECT:selection?.autoOpenJobId??job,...environment(),...restricted()&&{CLAUDE_CODE_RESTRICTED:"1"}}})'
    match = _HANDOFF.fullmatch(source)
    assert match is not None
    patched = _handoff(match)
    assert (
        'dispatchExtraArgs:[...(restricted()?["--restricted"]:[]),..._ccAgentsDispatchArgs()]'
        in patched
    )
    assert '...restricted()&&{CLAUDE_CODE_RESTRICTED:"1"}' in patched
