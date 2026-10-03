"""Regression coverage for updated upstream patch anchors."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ccpatch.agents_handoff import agents_view_handoff
from ccpatch.patches import (
    MODEL_SESSION_ONLY,
    MULTI_PROVIDER_SDK,
    STREAMING_THINKING,
    THINKING_SUMMARIES_NONINTERACTIVE_198,
    PatchError,
    PatchSet,
    thinking_expanded,
)


def _native_handoff() -> str:
    return (
        'function forward(ctx){return serialize({...parse(argv()).config,'
        'addDir:dirs(ctx),settingSources:sources()})}'
        'let defaults=make(session),extra=forward(context);'
        'if(flag("tengu_bg_leftarrow_inprocess",!0))try{return await mount(job,load,'
        '{dispatchDefaults:defaults,dispatchExtraArgs:extra,storageV5:storage})}'
        'catch(error){log(error)}let result=await spawn({args:["agents",...extra,...args(defaults)]});'
    )


def test_native_handoff_is_verified_without_duplicate_arguments() -> None:
    patch = agents_view_handoff((2, 1, 288))
    source = _native_handoff()
    assert patch.apply(source) == source
    for changed in [
        source.replace('dispatchExtraArgs:extra', 'dispatchExtraArgs:other'),
        source.replace('...extra,', ''),
        source.replace('settingSources:sources()', 'settingSources:[]'),
        source + source,
    ]:
        with pytest.raises(PatchError):
            patch.apply(changed)


def test_subagent_thinking_preserves_remote_worker_argument() -> None:
    patch = THINKING_SUMMARIES_NONINTERACTIVE_198.patches[1]
    source = (
        'function setting(){return settings().showThinkingSummaries??!1}'
        'function policy(thinking,{useExactTools:exact,forwardSubagentText:forward,'
        'isAsync:async,isNonInteractiveSession:noninteractive,sessionDisplayExplicit:explicit,'
        'isRemoteWorker:remote}){if(!noninteractive||exact||forward||async||thinking.type==="disabled")'
        'return thinking;if(!remote&&(explicit||thinking.display==="omitted"))return thinking;'
        'return {...thinking,display:"omitted",displayExplicit:!1}}'
    )
    result = PatchSet('test', (patch,)).apply(source)
    assert result == source.replace(
        'if(!noninteractive', 'if(setting()||!noninteractive'
    )


def test_model_picker_removes_default_action_with_policy_guard() -> None:
    patch = MODEL_SESSION_ONLY.patches[1]
    source = (
        'const props={onSelect:select,onSetDefault:(model)=>{if(denied(model)!==null)return;'
        'save.current=!0},onCancel:cancel,isStandaloneCommand:!0,showFastModeNotice:notice};'
    )
    result = PatchSet('test', (patch,)).apply(source)
    assert 'onSetDefault:' not in result
    assert 'onSelect:select,' in result
    assert 'showFastModeNotice:notice' in result
    assert 'Your pick applies to this session only.' in result


def test_nonstreaming_route_preserves_combined_deadline_signal() -> None:
    patch = next(
        p for p in MULTI_PROVIDER_SDK.patches if p.name == 'route-nonstreaming-fallback'
    )
    source = 'let result=await client.beta.messages.create({...request,stream:!1},{signal:AbortSignal.any([caller.signal,deadline.signal]),timeout:duration,...Object.keys(headers).length>0&&{headers:headers}}).withResponse();'
    result = PatchSet('test', (patch,)).apply(source)
    assert (
        'signal:AbortSignal.any([caller.signal,deadline.signal]),timeout:duration'
        in result
    )
    assert '_ccMultiProviderRoute(client,_ccRequest,_ccOptions)' in result
    assert result.endswith('.withResponse();')


@pytest.mark.parametrize('version', [(2, 1, 288), (2, 1, 999)])
def test_updated_sets_apply_to_native_capture(version: tuple[int, int, int]) -> None:
    path = os.environ.get('CCPATCH_288_SOURCE')
    if not path:
        pytest.skip('set CCPATCH_288_SOURCE to pristine 2.1.288 source')
    source = Path(path).read_text()
    assert 'VERSION:"2.1.288"' in source
    assert agents_view_handoff(version).apply(source) == source
    MODEL_SESSION_ONLY.apply(source)
    THINKING_SUMMARIES_NONINTERACTIVE_198.apply(source)
    STREAMING_THINKING.apply(thinking_expanded(version).apply(source))
    for patch in MULTI_PROVIDER_SDK.patches:
        if patch.name in {
            'route-nonstreaming-fallback',
            'surface-count-tokens-provider-errors',
        }:
            patched = PatchSet('test', (patch,)).apply(source)
            if patch.name == 'surface-count-tokens-provider-errors':
                assert '_ccEffectiveModel=Owe(r??tt())' in patched
    if version == (2, 1, 288):
        patched = MULTI_PROVIDER_SDK.apply(source)
        assert (
            'defaultHeaders:{"x-app":"cli","User-Agent":xw()["User-Agent"]}' in patched
        )
        assert (
            '_ccMultiProviderCatalogInfo(s)===null&&!e.optIn1mHonored&&!GIe(s)&&!B&&!e.recognized'
            in patched
        )
