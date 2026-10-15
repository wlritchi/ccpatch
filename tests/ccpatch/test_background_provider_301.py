"""Preserve native 2.1.301 provider boundaries and settings bookkeeping."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import (
    PatchSet,
    _provider_serialized_keys,
    background_provider_environment,
)


def _patch(name: str, source: str) -> str:
    patch = next(
        patch
        for patch in background_provider_environment((2, 1, 301)).patches
        if patch.name == name
    )
    return PatchSet(name='test', patches=(patch,)).apply(source)


def _module(name: str, body: str) -> str:
    return f'\n/* ccpatch-module:{name.encode().hex()} */\n{body}'


def test_group_names_resolve_in_their_own_module() -> None:
    # 2.1.301 reuses the cloud credential name for an unrelated Set in the
    # snapshot module.
    snapshot = _module(
        'snapshot.js',
        'var M6e=new Set(["ANTHROPIC_API_KEY"]);'
        'function ozt(){let e={};for(let n of M6e){let o=process.env[n];'
        'if(o===void 0)continue;'
        'if(o===""&&n!=="CLAUDE_SECURESTORAGE_CONFIG_DIR")continue;e[n]=o}return e}'
        'var rr=new Set([...s4,"CLAUDE_CODE_HOST_GATEWAY_LINEAGE","AWS_CONFIG_FILE"]);',
    )
    groups = _module(
        'groups.js',
        'var s4=["CLAUDE_CODE_USE_BEDROCK","CLAUDE_CODE_USE_VERTEX"],'
        'SB=["ANTHROPIC_BASE_URL","ANTHROPIC_BEDROCK_BASE_URL"],'
        'wB=["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN"],'
        'j5n=["CLAUDE_CODE_SKIP_BEDROCK_AUTH","CLAUDE_CODE_SKIP_VERTEX_AUTH"],'
        'Syt=["ANTHROPIC_MODEL","ANTHROPIC_DEFAULT_MODEL"],'
        'W5n=["ANTHROPIC_CUSTOM_MODEL_OPTION","ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"],'
        'rr=["AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY","AWS_SESSION_TOKEN"],'
        'TUt=[...rr,"AWS_PROFILE","GOOGLE_CLOUD_PROJECT"];',
    )
    other = _module('other.js', 'var rr=["session_end","pushed_out"];')
    keys = json.loads(_provider_serialized_keys(snapshot + groups + other))
    assert keys[:3] == [
        'ANTHROPIC_API_KEY',
        'CLAUDE_CODE_USE_BEDROCK',
        'CLAUDE_CODE_USE_VERTEX',
    ]
    assert 'AWS_SECRET_ACCESS_KEY' in keys
    assert 'GOOGLE_CLOUD_PROJECT' in keys
    assert 'CLAUDE_CODE_HOST_GATEWAY_LINEAGE' not in keys
    assert 'session_end' not in keys


def test_kept_settings_filter_keeps_native_bookkeeping(tmp_path: Path) -> None:
    source = (
        'filterSettingsEnv(e,n,r){let i=this.keptSettingsEnv(e,n,'
        'this.providerStripContext,{onGitKeyDropped:r});'
        'for(let[o,s]of Object.entries(i))this.recordOfferedEnvValue(o,s);'
        'if(n==="globalConfig"||n==="userSettings")for(let o of Object.keys(i))'
        'this.userTierNames.add(o.toUpperCase());return i}'
        'withoutInvalidSettingsEnv(e,n){return e}'
    )
    patched = _patch('filter-provider-settings-with-native-policy', source)
    node = shutil.which('node')
    assert node is not None
    script = tmp_path / 'settings.mjs'
    script.write_text(
        'import assert from "node:assert/strict";'
        'const calls=[];'
        'function _ccProviderValidateManaged(env,scope){calls.push(scope);'
        'if(scope==="policySettings"&&env.ANTHROPIC_API_KEY!=="requester")throw Error("policy conflict")}'
        'function _ccProviderFilterSettings(env,scope){const result={...env};'
        'if(scope!=="policySettings")delete result.ANTHROPIC_API_KEY;return result}'
        'class Settings{offered=new Map;userTierNames=new Set;providerStripContext={};'
        'recordOfferedEnvValue(key,value){this.offered.set(key,value)}'
        'keptSettingsEnv(env,scope,context,options){options.onGitKeyDropped?.();'
        'return scope==="projectSettings"?{}:{...env}}' + patched + '}'
        'const settings=new Settings;let dropped=0;'
        'assert.deepEqual(settings.filterSettingsEnv({ANTHROPIC_API_KEY:"disk",COLOR:"yes"},'
        '"userSettings",()=>{dropped++}),{COLOR:"yes"});'
        'assert.equal(dropped,1);'
        'assert.equal(settings.offered.get("ANTHROPIC_API_KEY"),"disk");'
        'assert(settings.userTierNames.has("ANTHROPIC_API_KEY"));'
        'assert.deepEqual(settings.filterSettingsEnv({COLOR:"no"},"projectSettings"),{});'
        'assert.throws(()=>settings.filterSettingsEnv({ANTHROPIC_API_KEY:"other"},"policySettings"),/policy conflict/);'
        'assert.deepEqual(settings.filterSettingsEnv({ANTHROPIC_API_KEY:"requester"},"policySettings"),{ANTHROPIC_API_KEY:"requester"});'
        'assert.equal(calls.length,4);'
    )
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_claimed_entry_keeps_native_callback_parameter() -> None:
    source = (
        'async function claim(payload,main,ready){Object.assign(process.env,payload.env),'
        'process.argv=payload.argv,refresh();ready?.(),settle();let{main:run}=await main;'
        'metric("spare_claim_apply_ms",performance.now()-start,start),await run()}'
    )
    patched = _patch('apply-provider-env-after-claimed-initializers', source)
    assert patched.startswith(
        'async function claim(payload,main,ready){'
        '_ccProviderWorkerEnv=_ccProviderCaptureTransport(payload.env);'
    )
    assert 'ready?.(),settle();' in patched


def test_persisted_state_drops_path_resolved_provider_env() -> None:
    view = _patch(
        'remove-provider-env-from-state-view',
        'bgIsolation:e.bgIsolation,providerEnv:e.providerEnv&&ibs(e.providerEnv,e.cwd),'
        'sessionPermissionRules:e.sessionPermissionRules',
    )
    assert (
        view
        == 'bgIsolation:e.bgIsolation,sessionPermissionRules:e.sessionPermissionRules'
    )
    schema = _patch(
        'remove-provider-env-from-state-schema',
        'bgIsolation:B(["none","worktree"]).optional().catch(void 0),'
        'providerEnv:fe(o(),o()).transform((e)=>{let n=abs(Gr(e,PR));'
        'return n&&GSr(n,"the saved job state (state.json providerEnv)")}).optional(),'
        'sessionPermissionRules:u({})',
    )
    assert 'providerEnv' not in schema
    assert 'sessionPermissionRules:u({})' in schema


def test_inprocess_handoff_keeps_live_turn_abort() -> None:
    source = (
        'if(k("tengu_bg_leftarrow_inprocess",!0))try{return await _Ie($o,to,'
        '{dispatchDefaults:On,dispatchExtraArgs:ur,...Ie?.autoOpenJobId!==void 0&&'
        '{autoOpenJobId:Ie.autoOpenJobId},originSpawn:Nn,storageV5:De,credentials:Ne,'
        'fleetNudgeStore:Ie?.fleetNudgeStore,...Ie?.abortLiveTurn&&'
        '{abortLiveTurn:Ie.abortLiveTurn,dropLateQueued:jn}})}catch(rr){l(rr)}'
        'let Hr=await JQ({args:["agents",...ur,...Oet(On)],env:{CLAUDE_AGENTS_SELECT:'
        'Ie?.autoOpenJobId??$o,...sJ(),...uv()&&{CLAUDE_CODE_RESTRICTED:"1"}}});'
        'function _ccProviderKeys(){return[]}'
        'function ozt(){let e={};for(let n of _ccProviderKeys())e[n]=process.env[n];return e}'
    )
    patched = _patch('carry-provider-env-to-agents-fallback', source)
    assert 'abortLiveTurn:Ie.abortLiveTurn,dropLateQueued:jn' in patched
    assert '...sJ(),CLAUDE_CODE_PROVIDER_ENV_TRANSIENT:JSON.stringify(ozt())' in patched


def test_native_background_provider_301() -> None:
    path = os.environ.get('CCPATCH_301_SOURCE')
    if not path:
        pytest.skip('set CCPATCH_301_SOURCE to pristine 2.1.301 source')
    source = Path(path).read_text()
    assert 'VERSION:"2.1.301"' in source
    patched = background_provider_environment((2, 1, 301)).apply(source)
    assert 'providerEnv:e.providerEnv&&ibs(' not in patched
    assert 'abortLiveTurn:Ie.abortLiveTurn,dropLateQueued:jn' in patched
    assert 'this.fullPassDroppedGitKey=!0' in patched
    assert 'this.userTierNames.add(o.toUpperCase())' in patched
    assert 'V(this.promptDraftFile,this.storageV5)' in patched
