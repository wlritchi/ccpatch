"""Check the streaming thinking preview anchors on the 2.1.301 messages view."""

from __future__ import annotations

import pytest

from ccpatch.patches import STREAMING_THINKING, PatchSet

_DEFINE = STREAMING_THINKING.patches[1]
_MOUNT = STREAMING_THINKING.patches[3]

# Excerpts of the messages view. 2.1.301 has a larger memo cache and moves
# the provider slot from index 39 to index 41.
_VIEW_288 = (
    'function ste(l){let u=w(44),m,p,h;if(u[0]!==l)({source:m,hidePlaceholder:p,...h}=l);'
    'let gt;if(u[39]!==$e)gt=r(jTt.Provider,{value:ue,children:[$e,qe,pt]}),'
    'u[39]=$e,u[43]=gt;else gt=u[43];return gt}'
)
_VIEW_301 = (
    'function yde(u){let d=w(46),m,f,h;if(d[0]!==u)({source:m,hidePlaceholder:f,...h}=u);'
    'let Je;if(d[41]!==Ze)Je=r(yzt.Provider,{value:Le,children:[nt,at,Ze]}),'
    'd[41]=Ze,d[45]=Je;else Je=d[45];return Je}'
)
_BINDINGS = (
    'e(Ps,{addMargin:f,param:d,isTranscriptMode:true,verbose:true});'
    'Re(u,(O)=>O.isLoading)??!1;'
    'ee=WZ(ge||!K?D:yx);'
)


@pytest.mark.parametrize("view", [_VIEW_288, _VIEW_301])
def test_preview_definition_precedes_messages_view(view: str) -> None:
    patched = PatchSet("define", (_DEFINE,)).apply(_BINDINGS + view)

    helper = patched.index("function _ccThinkingPreview({stream})")
    assert helper < patched.index("function ", helper + 1)
    assert "let current=Re(stream,state=>state.streamingThinking);" in patched
    assert 'let text=WZ(current?.thinking??"");' in patched
    assert 'e(Ps,{addMargin:true,param:{type:"thinking",thinking:text}' in patched


@pytest.mark.parametrize(
    ("view", "expected"),
    [
        (
            _VIEW_288,
            "r(jTt.Provider,{value:ue,children:"
            "[$e,qe,pt,r(_ccThinkingPreview,{stream:ue})]}),u[39]=$e",
        ),
        (
            _VIEW_301,
            "r(yzt.Provider,{value:Le,children:"
            "[nt,at,Ze,r(_ccThinkingPreview,{stream:Le})]}),d[41]=Ze",
        ),
    ],
)
def test_preview_mounts_after_streaming_text(view: str, expected: str) -> None:
    patched = PatchSet("mount", (_MOUNT,)).apply(view)

    assert expected in patched


def test_mount_requires_memo_slot_assignment() -> None:
    unrelated = "r(Ctx.Provider,{value:v,children:[a,b,c]});"
    assert _MOUNT.pattern.search(unrelated) is None
