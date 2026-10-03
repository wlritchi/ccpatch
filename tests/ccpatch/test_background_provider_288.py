"""Preserve native 2.1.288 provider boundaries and dispatch retries."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.patches import PatchSet, background_provider_environment


def _patch(name: str, source: str) -> str:
    patch = next(
        patch
        for patch in background_provider_environment((2, 1, 288)).patches
        if patch.name == name
    )
    return PatchSet(name='test', patches=(patch,)).apply(source)


def test_tracked_settings_keeps_native_policy_and_bookkeeping(tmp_path: Path) -> None:
    source = (
        'filterSettingsEnv(e,n){if(this.withholdsEnvFrom(n))return{};'
        'let r={...e};for(let[E,o]of Object.entries(r)){let s=E.toUpperCase(),'
        'i=o.trim(),_=this.settingsOfferedEnvValues.get(s);if(_)_.add(i);'
        'else this.settingsOfferedEnvValues.set(s,new Set([i]))}'
        'if(n==="globalConfig"||n==="userSettings")for(let E of Object.keys(r))'
        'this.userTierNames.add(E.toUpperCase());return r}'
        'isSettingsSourcedEnvValue(){}'
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
        'class Settings{settingsOfferedEnvValues=new Map;userTierNames=new Set;'
        'withholdsEnvFrom(scope){return scope==="projectSettings"}' + patched + '}'
        'const settings=new Settings;'
        'assert.deepEqual(settings.filterSettingsEnv({ANTHROPIC_API_KEY:"disk",COLOR:"yes"},"userSettings"),{COLOR:"yes"});'
        'assert(settings.settingsOfferedEnvValues.get("ANTHROPIC_API_KEY").has("disk"));'
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


def test_claimed_entry_preserves_timing_and_captures_before_native_settings() -> None:
    source = (
        'async function claim(payload,main){Object.assign(process.env,payload.env),'
        'process.argv=payload.argv;let{main:run}=await main;'
        'metric("spare_claim_apply_ms",performance.now()-start,start),await run()}'
    )
    patched = _patch('apply-provider-env-after-claimed-initializers', source)
    assert patched.startswith(
        'async function claim(payload,main){'
        '_ccProviderWorkerEnv=_ccProviderCaptureTransport(payload.env);'
    )
    patched = _patch('reset-provider-initialization-on-spare-claim', patched)
    patched = _patch('avoid-provider-reset-after-claimed-entry', patched)
    assert '_ccProviderAwaitingClaim=false;_ccProviderInitialized=false;' in patched
    assert '_ccProviderApplyWorkerFinal();' not in patched
    assert (
        'metric("spare_claim_apply_ms",performance.now()-start,start),await run()'
        in patched
    )


def test_native_background_provider_288() -> None:
    path = os.environ.get('CCPATCH_288_SOURCE')
    if not path:
        pytest.skip('set CCPATCH_288_SOURCE to pristine 2.1.288 source')
    source = Path(path).read_text()
    assert 'VERSION:"2.1.288"' in source
    patched = background_provider_environment((2, 1, 288)).apply(source)
    assert 'if(AF())e.CLAUDE_CODE_HOST_GATEWAY_LINEAGE="1";return e}' in patched
    assert (
        'let j={proto:Bc,op:"dispatch",d:{...e,nonce:S},providerEnvVersion:' in patched
    )
    assert 'x=await Fg(j,{timeoutMs:6000})' in patched
    assert 'this.settingsOfferedEnvValues.set(s,new Set([i]))' in patched
    assert 'this.userTierNames.add(E.toUpperCase())' in patched
    assert 'args:["agents",...So,...QVe(Oo)]' in patched
    assert (
        'Host-managed session requires a live host-managed provider context' in patched
    )
    assert '.providerEnv??{}' not in patched
