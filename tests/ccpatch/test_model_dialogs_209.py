"""Execute captured model handlers with inert React, settings, and dialog adapters."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import MULTI_PROVIDER_SDK, _attribution_function

_ROOT = Path(__file__).resolve().parents[2] / "build/sweep-resume"


@pytest.mark.parametrize("version", [208, 209, 210, 211])
@pytest.mark.parametrize("patched", [False, True], ids=["native", "sdk"])
def test_captured_model_dialogs(version: int, patched: bool, tmp_path: Path) -> None:
    path = _ROOT / f"2.1.{version}/linux-x64/original.js"
    runtime = shutil.which("node") or shutil.which("bun")
    if not path.is_file() or runtime is None:
        pytest.skip("requires captured .208-.211 linux-x64 source and node/bun")
    original = path.read_text()
    source = MULTI_PROVIDER_SDK.apply(original) if patched else original
    names = (
        {
            "n1o": "Inline",
            "LW_": "Picker",
            "UZr": "Switch",
            "jZr": "Save",
            "mIu": "CanConsent",
            "fIu": "dialogAvailable",
            "si": "background",
            "gs": "sessionOnly",
            "Dca": "Compiler",
            "qN": "React",
            "Ge": "select",
            "fo": "setter",  # codespell:ignore fo
            "os": "notifications",
            "ADt": "validate",
            "Ql": "remote",
            "Dwo": "needsConsent",
            "Tne": "needsConsent",  # codespell:ignore tne
            "Lwo": "needsConfirmation",
            "SWt": "effortConfirmation",
            "Rwo": "identity",
            "Xke": "noop",
            "tl": "fastEnabled",
            "IEe": "noop",
            "RLr": "saveEffort",
            "Lca": "noop",
            "Dct": "empty",
            "U$s": "empty",
            "we": "event",
            "zo": "writeSettings",
            "ht": "style",
            "T_e": "ModelMenu",
            "b$t": "Consent",
            "p8e": "Confirm",
            "Ica": "blocked",
        }
        if version == 208
        else {
            "m1o": "Inline",
            "l6_": "Picker",
            "QZr": "Switch",
            "ZZr": "Save",
            "MIu": "CanConsent",
            "PIu": "dialogAvailable",
            "ms": "sessionOnly",
            "Yca": "Compiler",
            "VN": "React",
            "We": "select",
            "po": "setter",
            "Zi": "notifications",
            "kDt": "validate",
            "Jl": "remote",
            "Wwo": "needsConsent",
            "Cne": "needsConsent",
            "Gwo": "needsConfirmation",
            "xWt": "effortConfirmation",
            "jwo": "identity",
            "Jke": "noop",
            "rl": "fastEnabled",
            "REe": "noop",
            "ULr": "saveEffort",
            "Kca": "noop",
            "$ct": "empty",
            "o1s": "empty",
            "Ae": "event",
            "Ko": "writeSettings",
            "gt": "style",
            "C_e": "ModelMenu",
            "T$t": "Consent",
            "m8e": "Confirm",
        }
    )
    if version == 210:
        names = {
            'vFo': 'Inline',
            'lX_': 'Picker',
            'vzr': 'Switch',
            'Azr': 'Save',
            'iSu': 'CanConsent',
            'oSu': 'dialogAvailable',
            'ms': 'sessionOnly',
            'Rda': 'Compiler',
            'XN': 'React',
            'Ge': 'select',
            'mo': 'setter',
            'bs': 'notifications',
            'RIt': 'validate',
            'fc': 'remote',
            'Xdo': 'needsConsent',
            'Lre': 'needsConsent',
            'Ydo': 'needsConfirmation',
            'N6t': 'effortConfirmation',
            'Kdo': 'identity',
            'xIe': 'noop',
            'sl': 'fastEnabled',
            'Zve': 'noop',
            'PPr': 'saveEffort',
            'Ida': 'noop',
            'Pst': 'empty',
            'Gps': 'empty',
            'Ae': 'event',
            'ei': 'writeSettings',
            'ht': 'style',
            'ibe': 'ModelMenu',
            'A1t': 'Consent',
            'bVe': 'Confirm',
        }
    if version == 211:
        names = {
            'q2o': 'Inline',
            'Web': 'Picker',
            'zKr': 'Switch',
            'KKr': 'Save',
            'yvu': 'CanConsent',
            'gvu': 'dialogAvailable',
            'ms': 'sessionOnly',
            'pma': 'Compiler',
            'mF': 'React',
            'Ve': 'select',
            'po': 'setter',
            'ys': 'notifications',
            '_Rt': 'validate',
            'Cc': 'remote',
            'omo': 'needsConsent',
            'rne': 'needsConsent',
            'nmo': 'needsConfirmation',
            'wqt': 'effortConfirmation',
            'rmo': 'identity',
            'XIe': 'noop',
            'sl': 'fastEnabled',
            'mAe': 'noop',
            'ZMr': 'saveEffort',
            'dma': 'noop',
            'hat': 'empty',
            'Yms': 'empty',
            'Ae': 'event',
            'Zo': 'writeSettings',
            'gt': 'style',
            'bbe': 'ModelMenu',
            'uNt': 'Consent',
            'Z8e': 'Confirm',
        }
    if version == 210:
        names.update(
            {
                'bc': 'gc',
                'Es': 'sessionOnly',
                'uj': 'X4',
                'OT': 'cH',
                'Ie': 'Re',
                'qe': 'Ve',
                '$': 'O',
                'd3': 'e3',
                'vb': 'bb',
                'sRe': 'kIe',
                'iRe': 'xIe',
                'CZ': 'yZ',
                'du': 'du',
                'WF': 'PP',
            }
        )
    if version == 211:
        names.update(
            {
                'vc': 'gc',
                'bs': 'sessionOnly',
                'Cj': 'X4',
                'n0': 'cH',
                'Ie': 'Re',
                'We': 'Ve',
                'O': 'O',
                'H3': 'e3',
                'Mb': 'bb',
                'xRe': 'kIe',
                'HRe': 'xIe',
                'GZ': 'yZ',
                'bu': 'du',
                'rB': 'PP',
            }
        )
    fragments: list[str] = []
    for name, normalized in names.items():
        if normalized not in {"Inline", "Picker", "Switch", "Save", "CanConsent"}:
            continue
        if normalized == "Picker":
            start = original.index(f"function {name}(")
            end = original.index(
                {
                    208: "function qPp(",
                    209: "function gMp(",
                    210: "function FNp(",
                    211: "function a4p(",
                }[version],
                start,
            )
            fragment = original[start:end]
        elif normalized == "Inline":
            start = original.index(f"function {name}(")
            end = original.index(
                {
                    208: "function Lca(",
                    209: "function Kca(",
                    210: "function Ida(",
                    211: "function dma(",
                }[version],
                start,
            )
            fragment = original[start:end]
        else:
            fragment = _attribution_function(original, name)
        # The SDK patch must not replace native selection or persistence logic.
        assert fragment in source
        fragments.append(fragment)
    command = re.search(
        r"([\w$]+)=async\(e,t,r\)=>\{if\(r=r\?\.trim\(\)\|\|\"\",[\s\S]*?\};var ",
        original,
    )
    assert command is not None
    command_body = command[0].removesuffix(";var ")
    assert command_body in source
    fragments.append("const " + command_body + ";")
    names[command[1]] = "command"
    text = "\n".join(fragments)
    # Rename identifiers only. Keep messages and model IDs unchanged.
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|[A-Za-z_$][\w$]*')
    text = tokens.sub(lambda match: names.get(match[0], match[0]), text)
    script = tmp_path / "model-dialogs.cjs"
    script.write_text(f"const version={version};\n" + _ADAPTERS + text + _SCENARIOS)
    result = subprocess.run(  # noqa: S603 - Captured handlers and inert local adapters.
        [runtime, str(script)], capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"version": version, "ok": True}


_ADAPTERS = r'''
const assert=require("node:assert/strict");
let bg=true, only=false, consent=false, confirm=false, remoteClient=null;
let state, settings, writes, messages, hooks, cursor, effects, compiler, rendered;
const ModelMenu="menu", Consent="consent", Confirm="confirm", blocked="blocked";
const X4={jsx:(type,props)=>({type,props})}, style={bold:x=>String(x)};
const background=()=>bg, sessionOnly=()=>only, select=fn=>fn(state);
const setState=fn=>{state=fn(state)}, setter=()=>setState, gc=()=>({getState:()=>state});
const noop=()=>{}, empty=()=>"", identity=x=>x, cH=identity, Re=identity;
const event=noop, Ve=noop, O=noop, e3=noop, saveEffort=noop;
const notifications=()=>({addNotification:noop}), fastEnabled=()=>false;
const needsConsent=()=>consent, needsConfirmation=()=>confirm, effortConfirmation=()=>false;
const dialogAvailable=dialog=>typeof dialog==="function", remote=()=>remoteClient, bb=()=>true;
const validate=async model=>({ok:true,model:model==="default"?null:model});
const writeSettings=(scope,value)=>{assert.equal(scope,"userSettings");writes.push(value);Object.assign(settings,value)};
const Compiler={c:n=>compiler??=Array(n).fill(Symbol("empty"))};
const React={
 useState(initial){const i=cursor++;if(!(i in hooks))hooks[i]=initial;return [hooks[i],value=>hooks[i]=value]},
 useRef(initial){const i=cursor++;return hooks[i]??={current:initial}},
 useEffect(fn){effects.push(fn)}
};
const kIe=[],xIe=[],yZ=[],du=identity, PP=()=>true;
const reset=()=>{
 state={mainLoopModel:"claude-sonnet-4-6",mainLoopModelForSession:"old-override",fastMode:false,effortValue:"medium",other:"kept"};
 settings={model:"saved-before",env:{ANTHROPIC_API_KEY:"synthetic"},attribution:{commit:"custom"}};
 writes=[];messages=[];hooks=[];cursor=0;effects=[];compiler=null;consent=false;confirm=false;remoteClient=null;
};
const done=(...args)=>messages.push(args);
const render=(component,props)=>{cursor=0;effects=[];rendered=component(props);return rendered};
const flush=async()=>{for(const effect of effects)effect();await new Promise(resolve=>setImmediate(resolve))};
const checkSwitch=(model,persist)=>{
 assert.equal(state.mainLoopModel,model);assert.equal(state.mainLoopModelForSession,null);
 assert.equal(state.other,"kept");assert.equal(writes.length,persist?1:0);
 assert.equal(settings.model,persist?(model??undefined):"saved-before");
 assert.deepEqual(settings.env,{ANTHROPIC_API_KEY:"synthetic"});
 assert.deepEqual(settings.attribution,{commit:"custom"});
 assert.match(messages.at(-1)[0],persist?/saved as your default/:/for this session only/);
};
'''

_SCENARIOS = r'''
(async()=>{
 for(bg of [false,true])for(only of [false,true]){
  for(const model of ["claude-opus-4-8","openai:gpt-6-astra","zai:glm-5.3",null]){
   reset();render(Inline,{args:model??"default",onDone:done});await flush();
   checkSwitch(model,!only&&(version>=209||!bg));
  }
 }
 only=false;
 for(bg of [false,true]){
  reset();const entry=await command(done,{messages:[]},"");
  if(bg&&version===208){assert.equal(entry,undefined);assert.match(messages[0][0],/Can't open the model picker/)}
  else assert.equal(entry.type,Picker);
  for(const main of [false,true])for(const dialog of [undefined,()=>{}]){
   assert.equal(CanConsent({isMainThread:main,requestDialog:dialog}),main&&!!dialog&&(version>=209||!bg));
  }
  for(const save of [false,true]){
   reset();let menu=render(Picker,{onDone:done,hasConversationMessages:true});
   assert.equal(menu.props.skipSettingsWrite,true);
   if(save)menu.props.onSetDefault("openai:gpt-6-astra");
   menu.props.onSelect("openai:gpt-6-astra","high");checkSwitch("openai:gpt-6-astra",save);
   assert.equal(state.effortValue,"high");
  }
  for(const accept of [false,true]){
   reset();consent=true;
   render(Inline,{args:"claude-fable-5",onDone:done});await flush();
   assert.equal(writes.length,0);assert.equal(state.mainLoopModel,"claude-sonnet-4-6");
   const dialog=render(Inline,{args:"claude-fable-5",onDone:done});
   if(bg&&version===208){assert.equal(dialog,null);assert.equal(messages[0][0],blocked)}
   else {
    assert.equal(dialog.type,Consent);dialog.props.onDone(accept?"consent":"cancel");
    if(accept)checkSwitch("claude-fable-5",version>=209||!bg);
    else {assert.equal(writes.length,0);assert.equal(state.mainLoopModel,"claude-sonnet-4-6")}
   }
   reset();consent=true;
   let menu=render(Picker,{onDone:done,hasConversationMessages:true});
   menu.props.onSetDefault("claude-fable-5");menu.props.onSelect("claude-fable-5");
   assert.equal(writes.length,0);
   const pickerDialog=render(Picker,{onDone:done,hasConversationMessages:true});
   if(bg&&version===208){assert.equal(pickerDialog.type,ModelMenu);assert.equal(messages[0][0],blocked)}
   else {
    assert.equal(pickerDialog.type,Consent);pickerDialog.props.onDone(accept?"consent":"cancel");
    if(accept)checkSwitch("claude-fable-5",true);
    else {
     consent=false;menu=render(Picker,{onDone:done,hasConversationMessages:true});
     menu.props.onSelect("zai:glm-5.3");checkSwitch("zai:glm-5.3",false);
    }
   }
  }
  reset();confirm=true;
  let menu=render(Picker,{onDone:done,hasConversationMessages:true});
  menu.props.onSetDefault("openai:gpt-6-astra");menu.props.onSelect("openai:gpt-6-astra");
  assert.equal(writes.length,0);
  let dialog=render(Picker,{onDone:done,hasConversationMessages:true});
  assert.equal(dialog.type,Confirm);dialog.props.onCancel();confirm=false;
  menu=render(Picker,{onDone:done,hasConversationMessages:true});
  menu.props.onSelect("zai:glm-5.3");checkSwitch("zai:glm-5.3",false);
  reset();consent=true;remoteClient={sendControlRequest:()=>{throw Error("must consent locally")}};
  render(Inline,{args:"claude-fable-5",onDone:done});await flush();
  assert.equal(writes.length,0);assert.match(messages[0][0],/cloud session/);
 }
 console.log(JSON.stringify({version,ok:true}));
})().catch(error=>{console.error(error);process.exitCode=1});
'''
