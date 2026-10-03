"""Keep native replay invalidation and file locks in retraction archives."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.retractions import RetractionPatchError, patch_retractions

_SOURCE = '''
async function remove(e,n){let r=flag()&&n!==void 0?n:void 0;await writer().removeMessageByUuid(e,r)}
async function continuation(e,n){let r=flag()&&n!==void 0?n:void 0;if(!(await store().sessionMessages(session(),r)).has(e))return;await writer().removeMessageByUuid(e,r)}
onTombstone:(msg)=>{if(apply({type:"remove-by-uuid",uuid:msg.uuid}),this.stream.transcriptRetractedUuids.delete(msg.uuid))return;remove(msg.uuid,storage)}
for(let uuid of ids)apply({type:"remove-by-uuid",uuid:uuid}),this.stream.transcriptRetractedUuids.add(uuid),remove(uuid,storage)
function evict(uuid){let index=r.findLastIndex((row)=>row.uuid===uuid);if(index<0)return;metric("tengu_tombstone_persisted_removal",{message_type:safe(row.type)}),remove(uuid,s)}
async removeMessageByUuid(e,n){return this.trackWrite(async()=>{let r=this.sessionFile;if(r===null)return;return this.replaySources.delete(r),this.queuedToolResults.clear(),this.enqueueRemove(r,e,n)})}
async performRemoveByUuid(e,n,r){using s=await lock(e);let g=r!==void 0?key(e):void 0;if(r!==void 0)return this.removeByUuidV5(r,g,n);events.push("purge")}
async removeByUuidV5(){}
{removeUuid:n,resolve:s,storageV5:r}
this.performRemoveByUuid(e,K.removeUuid,K.storageV5)
async appendEntry(e,n=this.store.getSessionId(),r,s,g,h){let rows=await this.store.sessionMessages(n,s),sidechain=e.isSidechain;switch(policy[e.type]){}}
var policy={user:"dedup-transcript",assistant:"dedup-transcript"};
fireMirror(e,n){for(let r of this.mirrors)r(e,n)}
if(S){if(isTranscript(S))S=await resolveTranscript(S,{onTranscriptUnreadable:(error)=>{V=error},storageV5:r.storageV5});}
'''


def _method(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset + len(start))]


def test_retraction_288_preserves_native_invalidation_and_lock(tmp_path: Path) -> None:
    patched = patch_retractions(_SOURCE)
    assert (
        'this.performRemoveByUuid(e,K.removeUuid,K.storageV5,K.ccpatchSessionId)'
        in patched
    )
    assert 'restore(S.fullPath,flag()?r.storageV5:void 0,key(S.fullPath))' in patched
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is unavailable')
    script = tmp_path / 'retraction-288.mjs'
    script.write_text(
        'import assert from "node:assert/strict";\n'
        + 'class Writer{'
        + _method(patched, 'async removeMessageByUuid(', 'async performRemoveByUuid(')
        + _method(patched, 'async performRemoveByUuid(', 'async removeByUuidV5(')
        + '''
store={getSessionId:()=>"session"};sessionFile="file";
replaySources=new Map([["file",1],["other",2]]);queuedToolResults=new Map([["tool",1]]);
shouldSkipPersistence(){return false}trackWrite(fn){return fn()}
enqueueRemove(...args){events.push(["enqueue",...args])}
removeByUuidV5(...args){events.push(["v5",...args])}
}
const events=[];
async function lock(path){events.push(["lock",path]);return {[Symbol.dispose](){events.push("unlock")}}}
const key=path=>"key:"+path;
let fail=false;
const __ccpatchRetractionStorage={async replace(...args){events.push(["archive",...args]);if(fail)throw Error("disk failure")}};
const writer=new Writer();
await writer.removeMessageByUuid("message");
assert.equal(writer.replaySources.has("file"),false);
assert.equal(writer.replaySources.get("other"),2);
assert.equal(writer.queuedToolResults.size,0);
assert.deepEqual(events.splice(0),[["enqueue","file","message",undefined]]);
await writer.performRemoveByUuid("file","message",undefined,"queued-session");
assert.deepEqual(events.splice(0),[["lock","file"],["archive","file","queued-session","message"],"unlock"]);
fail=true;
await writer.performRemoveByUuid("file","message");
assert.deepEqual(events.splice(0),[["lock","file"],["archive","file","session","message"],"purge","unlock"]);
const remote={};
await writer.performRemoveByUuid("file","message",remote);
assert.deepEqual(events.splice(0),[["lock","file"],["v5",remote,"key:file","message"],"unlock"]);
'''
    )
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_retraction_288_rejects_unknown_lock_protocol() -> None:
    with pytest.raises(RetractionPatchError, match='serialized archive conversion'):
        patch_retractions(
            _SOURCE.replace('using s=await lock(e);', 'lockWithoutRelease(e);')
        )


def test_retraction_288_keeps_legacy_writer_anchors() -> None:
    source = (
        _SOURCE.replace(
            'this.replaySources.delete(r),this.queuedToolResults.clear(),', ''
        )
        .replace('using s=await lock(e);let g=', 'let s=')
        .replace('this.removeByUuidV5(r,g,n)', 'this.removeByUuidV5(r,s,n)')
        .replace('K.removeUuid,K.storageV5', 'B.removeUuid,B.storageV5')
        .replace(
            'if(S){if(isTranscript(S))S=await resolveTranscript(S,',
            'if(y){if(isTranscript(y))y=await resolveTranscript(y,',
        )
    )
    patched = patch_retractions(source)
    assert (
        'this.performRemoveByUuid(e,B.removeUuid,B.storageV5,B.ccpatchSessionId)'
        in patched
    )
    assert 'restore(y.fullPath,flag()?r.storageV5:void 0,key(y.fullPath))' in patched
    assert 'let s=r!==void 0?key(e):void 0;if(r===void 0)try{' in patched


def test_native_retraction_288() -> None:
    path = os.environ.get('CCPATCH_288_SOURCE')
    if not path:
        pytest.skip('set CCPATCH_288_SOURCE to pristine 2.1.288 source')
    source = Path(path).read_text()
    assert 'VERSION:"2.1.288"' in source
    patched = patch_retractions(source)
    assert (
        'this.replaySources.delete(r),this.queuedToolResults.clear(),this.enqueueRemove(r,e,n)'
        in patched
    )
    assert (
        'using s=await wM(e);let g=r!==void 0?Yy(e):void 0;if(r===void 0)try{await __ccpatchRetractionStorage.replace'
        in patched
    )
    assert (
        'this.performRemoveByUuid(e,K.removeUuid,K.storageV5,K.ccpatchSessionId)'
        in patched
    )
    assert 'restore(S.fullPath,F()?r.storageV5:void 0,Yy(S.fullPath))' in patched
    with pytest.raises(RetractionPatchError, match='missing transcript removal'):
        patch_retractions(patched)
