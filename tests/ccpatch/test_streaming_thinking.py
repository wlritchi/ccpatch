"""Check incremental thinking state and the live Markdown preview."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import STREAMING_THINKING, PatchError, PatchSet


def _node(tmp_path: Path, source: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    script = tmp_path / "thinking.mjs"
    script.write_text('import assert from "node:assert/strict";\n' + source)
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "names", [("event", "callbacks", "context"), ("e$", "n$", "r$")]
)
def test_streaming_thinking_events(tmp_path: Path, names: tuple[str, str, str]) -> None:
    event, callbacks, context = names
    source = (
        f"function reduce({event},{callbacks},{context}){{"
        f"let{{onSetStreamMode:mode}}={callbacks};mode?.({event}.event.type)}}"
    )
    patched = PatchSet("events", (STREAMING_THINKING.patches[0],)).apply(source)
    _node(
        tmp_path,
        patched
        + '''
let state=null;
const modes=[];
const callbacks={onStreamingThinking:update=>state=update(state),onSetStreamMode:m=>modes.push(m)};
const send=event=>reduce({event},callbacks,{});
send({type:"message_start"});
send({type:"content_block_start",content_block:{type:"thinking",thinking:"First "}});
send({type:"content_block_delta",delta:{type:"thinking_delta",thinking:"**part**"}});
assert.deepEqual(state,{thinking:"First **part**",isStreaming:true});
send({type:"content_block_delta",delta:{type:"thinking_delta",estimated_tokens:4}});
assert.equal(state.thinking,"First **part**");
send({type:"content_block_delta",delta:{type:"thinking_delta",thinking:" second"}});
assert.equal(state.thinking,"First **part** second");
send({type:"content_block_stop"});
assert.equal(state.thinking,"First **part** second","keep partial thinking for interruption salvage");
send({type:"content_block_start",content_block:{type:"thinking",thinking:""}});
assert.equal(state.thinking,"");
send({type:"content_block_delta",delta:{type:"thinking_delta",thinking:"x".repeat(1000001)}});
assert.equal(state.thinking.length,1000000);
send({type:"content_block_start",content_block:{type:"redacted_thinking",data:"SECRET"}});
send({type:"content_block_delta",delta:{type:"thinking_delta",thinking:"secret"}});
assert.equal(state,null);
send({type:"content_block_start",content_block:{type:"thinking"}});
send({type:"content_block_delta",delta:{type:"thinking_delta",thinking:"old"}});
send({type:"message_start"});
assert.equal(state,null);
send({type:"content_block_start",content_block:{type:"text",text:""}});
assert.equal(state,null);
assert.equal(modes.length,14);
reduce({event:{type:"message_start"}},{},{});
''',
    )


def test_preview_renders_only_live_thinking(tmp_path: Path) -> None:
    source = (
        'jsx(Thinking,{addMargin:a,param:p,isTranscriptMode:true,verbose:true});'
        'subscribe(turn,(state)=>state.isLoading)??!1;'
        'value=deferred(flag||!other?messages:empty);'
        'function view(props){let cache=memo(40),source,hidden,rest;'
        'if(cache[0]!==props)({source:source,hidePlaceholder:hidden,...rest}=props);}'
    )
    patched = PatchSet("view", (STREAMING_THINKING.patches[1],)).apply(source)
    helper = patched[
        patched.index("function _ccThinkingPreview") : patched.index("function view")
    ]
    _node(
        tmp_path,
        '''
let state=null;
const subscribe=(stream,select)=>stream?select({streamingThinking:state}):undefined;
const deferred=value=>value;
const jsx=(component,props)=>({component,props});
const Thinking=()=>{};
'''
        + helper
        + '''
assert.equal(_ccThinkingPreview({stream:null}),null);
state={thinking:"**partial**",isStreaming:true};
const view=_ccThinkingPreview({stream:{}});
assert.equal(view.component,Thinking);
assert.equal(view.props.param.thinking,"**partial**");
assert.equal(view.props.isTranscriptMode,true);
assert.equal(view.props.verbose,true);
state={thinking:"complete",isStreaming:false};
assert.equal(_ccThinkingPreview({stream:{}}),null);
state=null;
assert.equal(_ccThinkingPreview({stream:{}}),null);
''',
    )


def test_streaming_thinking_version_and_anchor_failures() -> None:
    assert not STREAMING_THINKING.applies_to((2, 1, 279))
    assert STREAMING_THINKING.applies_to((2, 1, 280))
    assert STREAMING_THINKING.applies_to((2, 1, 999))
    with pytest.raises(PatchError, match="missing preview binding"):
        STREAMING_THINKING.apply("unrecognized upstream")
