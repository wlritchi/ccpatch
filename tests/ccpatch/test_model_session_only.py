"""Keep model commands out of persistent settings."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import (
    MODEL_SESSION_ONLY,
    PatchError,
    _attribution_function,
    default_patch_sets,
)

_HEADER = (
    "Switch between Claude models. Your pick becomes the default for new sessions."
)


def _fixture(suffix: str = "") -> str:
    return (
        f'function command(model{suffix}){{let save{suffix}=!sessionOnly{suffix}();'
        f'clear{suffix}(null);let result{suffix}=switchModel{suffix}('
        f'session{suffix},model{suffix},()=>store{suffix}.getState(),setState{suffix},'
        f'save{suffix},"command",undefined);return result{suffix}}}'
        f'const props={{onSelect:select{suffix},onSetDefault:(model{suffix})=>'
        f'{{saveRef{suffix}.current=!0}},onCancel:cancel{suffix},'
        'isStandaloneCommand:!0,skipSettingsWrite:!0};'
        f'const header="{_HEADER}";'
    )


def _node(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    script = tmp_path / "model-session.mjs"
    script.write_text('import assert from "node:assert/strict";\n' + source)
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("suffix", ["", "$", "Renamed"])
def test_session_only_structural_variants(suffix: str, tmp_path: Path) -> None:
    patched = MODEL_SESSION_ONLY.apply(_fixture(suffix))
    _node(
        tmp_path,
        f'''
let state={{mainLoopModel:"old"}},saveRef{suffix}={{current:false}};
const session{suffix}={{}},store{suffix}={{getState:()=>state}};
const setState{suffix}=value=>{{state=value}},clear{suffix}=()=>{{}},cancel{suffix}=()=>{{}};
const sessionOnly{suffix}=()=>{{throw Error("must not consult persistence flag")}};
const switchModel{suffix}=(session,model,get,set,save)=>{{assert.equal(save,false);set({{...get(),mainLoopModel:model}})}};
const select{suffix}=model=>{{assert.equal(saveRef{suffix}.current,false);state.mainLoopModel=model}};
'''
        + patched
        + '''
for(const model of ["claude-opus-5-5","openai:gpt-6-astra",null]){
 command(model);assert.equal(state.mainLoopModel,model);
 assert.equal(props.onSetDefault,undefined);assert.equal(props.skipSettingsWrite,true);
 props.onSelect(model);assert.equal(state.mainLoopModel,model);
}
assert.match(props.headerText,/this session only/);
assert.match(header,/default for new sessions/);
''',
    )


def test_session_only_version_and_anchor_checks() -> None:
    assert MODEL_SESSION_ONLY not in default_patch_sets((2, 1, 279))
    assert MODEL_SESSION_ONLY not in default_patch_sets(None)
    for version in [(2, 1, 280), (2, 1, 999)]:
        assert MODEL_SESSION_ONLY in default_patch_sets(version)
    for source in [
        "unknown upstream",
        _fixture() + _fixture(),
        _fixture().replace('"command"', '"changed"'),
    ]:
        with pytest.raises(PatchError):
            MODEL_SESSION_ONLY.apply(source)
    with pytest.raises(PatchError):
        MODEL_SESSION_ONLY.apply(MODEL_SESSION_ONLY.apply(_fixture()))


@pytest.mark.parametrize("patched", [False, True], ids=["native", "session-only"])
def test_native_model_commands(patched: bool, tmp_path: Path) -> None:
    path = os.environ.get("CCPATCH_280_SOURCE")
    if not path:
        pytest.skip("set CCPATCH_280_SOURCE to pristine 2.1.280 source")
    source = Path(path).read_text()
    assert 'VERSION:"2.1.280"' in source
    if patched:
        source = MODEL_SESSION_ONLY.apply(source)
    command_module = source[
        source.rindex('function Dt(', 0, source.index('function Jo({getMessages:')) :
    ]
    fragments = [
        command_module[
            command_module.index('function Jo(') : command_module.index('function jo(')
        ],
        command_module[
            command_module.index('function Be(') : command_module.index('function ao(')
        ],
        *[
            _attribution_function(
                command_module[: command_module.index('function Jo(')], name
            )
            for name in ["Dt", "Rt", "Ct"]
        ],
    ]
    switch = re.search(r'async function Dmt\([^;]+[\s\S]*?(?=var R=3000;)', source)
    assert switch is not None
    fragments.append(switch[0])
    _node(
        tmp_path,
        f"const patched={str(patched).lower()};\n"
        + _ADAPTERS
        + "\n".join(fragments)
        + _SCENARIOS,
    )


_ADAPTERS = r'''
let state,settings,writes,messages,slots,cursor,effects,compiler,consent,confirmation,blocked,invalid,only;
const y=Symbol("empty"),qhe="Set model to ",Khe="Kept model as ";
const Sfe="picker",aKe="consent",hY="confirm",s4="pending";
const e=(type,props)=>({type,props}),j=fn=>fn(state),pr=()=>({getState:()=>state});
const Xt=()=>fn=>{state=fn(state)},Xr=()=>({addNotification:()=>{}});
const w=n=>compiler??=Array(n).fill(y);
const g=initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==="function"?initial():initial;return [slots[i],value=>slots[i]=value]};
const T=initial=>{const i=cursor++;return slots[i]??={current:initial}};
const A=fn=>effects.push(fn),uZ=()=>undefined,Ut=()=>only,ir=()=>null,na=()=>false;
const khn=()=>consent,kt=x=>x,mh=x=>x,Sy=()=>"default",Ky=x=>x,tb=x=>x??"default";
const _ae=async model=>invalid?{ok:false,message:"invalid model"}:{ok:true,model:model==="default"?null:model};
const qy=(session,fn)=>fn(),Dw=async()=>({decision:blocked?"block":"proceed",skipConfirm:false,messages:[]});
const mH=s=>s.mainLoopModel,BN=()=>"blocked",Ehn=()=>confirmation,RSn=()=>null;
const wTe=(model,effort)=>effort===undefined?undefined:{level:effort,fromUltracode:false,ultracode:false};
const zbe=()=>false,_l=s=>s.sessionEffort,o5=x=>x,W9=()=>null,QI=x=>x;
const H2=(effort,model,save)=>{if(save){writes.push({effort});settings.effort=effort}};
const o8e=async model=>{writes.push({model});settings.model=model??undefined;return {kind:"saved"}};
const s8e=()=>"",cYn=()=>"",uYn=()=>"",yae=()=>"",to=()=>false,Uk=()=>false;
const IQ=()=>{},Ih=()=>{},i=()=>{},f=()=>{},_=()=>{},ao=()=>{},Bk=()=>{},vt=x=>x;
const b=x=>x,aV=s=>s.mainLoopModel,qc=x=>x,l=x=>String(x),ce=x=>x,d=x=>{throw x};
const reset=()=>{
 state={mainLoopModel:"before",mainLoopModelForSession:"override",sessionEffort:"medium",fastMode:false,other:"kept"};
 settings={model:"saved-before",other:"kept"};writes=[];messages=[];slots=[];cursor=0;effects=[];compiler=null;
 consent=false;confirmation=false;blocked=false;invalid=false;only=false;
};
const done=message=>messages.push(message),props={onDone:done,getMessages:()=>[],session:{}};
const render=(component,extra={})=>{cursor=0;effects=[];return component({...props,...extra})};
const flush=async()=>{for(const fn of effects)fn();effects=[];await new Promise(resolve=>setImmediate(resolve))};
const check=(model,persist)=>{
 assert.equal(state.mainLoopModel,model);assert.equal(state.mainLoopModelForSession,null);
 assert.equal(state.other,"kept");assert.equal(settings.other,"kept");
 assert.equal(settings.model,persist?(model??undefined):"saved-before");
 assert.equal(writes.filter(x=>"model" in x).length,persist?1:0);
 assert.match(messages.at(-1),persist?/saved as your default/:/for this session only/);
};
'''

_SCENARIOS = r'''
for(const model of ["claude-opus-5-5","openai:gpt-6-astra","zai:glm-5.3",null]){
 for(const sessionOnly of [false,true]){
  reset();only=sessionOnly;render(Be,{args:model??"default"});await flush();check(model,!patched&&!only);
 }
 reset();let menu=render(Jo);assert.equal(menu.props.skipSettingsWrite,true);
 assert.equal(typeof menu.props.onSetDefault,patched?"undefined":"function");
 menu.props.onSetDefault?.(model);menu.props.onSelect(model,"high");await flush();check(model,!patched);
 assert.equal(state.sessionEffort,"high");assert.equal(writes.length,patched?0:2);
 for(const picker of [false,true])for(const accept of [false,true]){
  reset();consent=true;
  if(picker){menu=render(Jo);menu.props.onSetDefault?.(model);menu.props.onSelect(model)}
  else {render(Be,{args:model??"default"});await flush()}
  assert.equal(writes.length,0);assert.equal(state.mainLoopModel,"before");
  let dialog=render(picker?Jo:Be,{args:model??"default"});assert.equal(dialog.type,aKe);
  dialog.props.onDone(accept?"consent":"cancel");await flush();
  if(accept)check(model,!patched);else assert.equal(state.mainLoopModel,"before");
 }
 for(const picker of [false,true])for(const accept of [false,true]){
  reset();confirmation=true;
  if(picker){menu=render(Jo);menu.props.onSetDefault?.(model);menu.props.onSelect(model)}
  else render(Be,{args:model??"default"});
  await flush();assert.equal(writes.length,0);assert.equal(state.mainLoopModel,"before");
  const dialog=render(picker?Jo:Be,{args:model??"default"});assert.equal(dialog.type,hY);
  if(accept){await dialog.props.onConfirm();await flush();check(model,!patched)}
  else {dialog.props.onCancel();assert.equal(writes.length,0);assert.equal(state.mainLoopModel,"before")}
 }
}
reset();invalid=true;render(Be,{args:"not-a-model"});await flush();assert.equal(writes.length,0);assert.equal(state.mainLoopModel,"before");
for(const picker of [false,true]){
 reset();blocked=true;
 if(picker){const menu=render(Jo);menu.props.onSetDefault?.("new");menu.props.onSelect("new")}
 else render(Be,{args:"new"});
 await flush();assert.equal(writes.length,0);assert.equal(state.mainLoopModel,"before");
}
'''
