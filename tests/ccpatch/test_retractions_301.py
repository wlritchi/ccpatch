"""Keep retraction archives correct with the 2.1.301 transcript writer."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.retractions import RetractionPatchError, patch_retractions

_RUNTIME = (Path(__file__).parents[2] / 'src/ccpatch/retraction_runtime.js').read_text()

_SOURCE = '''
async function remove(e,n){let r=flag()&&n!==void 0?n:void 0;await writer().removeMessageByUuid(e,r)}
async function continuation(e,n){let r=flag()&&n!==void 0?n:void 0;if(!(await store().sessionMessages(session(),r)).has(e))return;await writer().removeMessageByUuid(e,r)}
onTombstone:(msg)=>{if(apply({type:"remove-by-uuid",uuid:msg.uuid}),this.stream.transcriptRetractedUuids.delete(msg.uuid))return;remove(msg.uuid,storage)}
for(let uuid of ids)apply({type:"remove-by-uuid",uuid:uuid}),this.stream.transcriptRetractedUuids.add(uuid),remove(uuid,storage)
function sdk({conversation:e,messages:n,persistSession:r,storageV5:s}){let h=0;function flush(){return null}function evict(uuid){let index=n.findLastIndex((row)=>row.uuid===uuid);if(index!==-1){let row=n[index];if(n.splice(index,1),h>index){if(h--,r)metric("tengu_tombstone_persisted_removal",{message_type:safe(row.type)}),remove(uuid,s)}}let other=e.findLastIndex((row)=>row.uuid===uuid);if(other!==-1)e.splice(other,1)}return{evict,advance(){h=n.length}}}
async removeMessageByUuid(e,n){return this.trackWrite(async()=>{let r=this.sessionFile;if(r===null)return;return this.restartReplayIndex(r,!0),this.queuedToolResults.clear(),this.enqueueRemove(r,e,n)})}
async performRemoveByUuid(e,n,r){let s=r!==void 0?key(e):void 0;if(v5()&&r!==void 0&&s!==void 0)return this.removeByUuidV5(r,s,n);if(diskless())return;events.push("purge")}
async removeByUuidV5(){}
{removeUuid:n,resolve:s,storageV5:r}
this.performRemoveByUuid(e,K.removeUuid,K.storageV5)
async appendEntry(e,n=this.store.getSessionId(),r,s,g,h){let rows=await this.store.sessionMessages(n,s),sidechain=e.isSidechain;switch(policy[e.type]){}}
var policy={user:"dedup-transcript",assistant:"dedup-transcript"};
fireMirror(e,n){for(let r of this.mirrors)r(e,n)}
if(S){if(isTranscript(S))S=await resolveTranscript(S,{onTranscriptUnreadable:(error)=>{V=error},storageV5:r.storageV5});}
'''


def _between(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset + len(start))]


def _node(tmp_path: Path, name: str, code: str) -> None:
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is unavailable')
    script = tmp_path / name
    script.write_text('import assert from "node:assert/strict";\n' + code)
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_retraction_301_writer_skips_diskless_archive(tmp_path: Path) -> None:
    patched = patch_retractions(_SOURCE)
    assert 'if(r===void 0&&!diskless())try{' in patched
    _node(
        tmp_path,
        'retraction-301-writer.mjs',
        'class Writer{'
        + _between(patched, 'async removeMessageByUuid(', 'async performRemoveByUuid(')
        + _between(patched, 'async performRemoveByUuid(', 'async removeByUuidV5(')
        + '''
store={getSessionId:()=>"session"};sessionFile="file";queuedToolResults=new Map([["tool",1]]);
shouldSkipPersistence(){return false}trackWrite(fn){return fn()}
restartReplayIndex(...args){events.push(["restart",...args])}
enqueueRemove(...args){events.push(["enqueue",...args])}
removeByUuidV5(...args){events.push(["v5",...args])}
}
const events=[];
const key=path=>"key:"+path;
let disk=false,fail=false;
const diskless=()=>disk,v5=()=>true;
const __ccpatchRetractionStorage={async replace(...args){events.push(["archive",...args]);if(fail)throw Error("disk failure")}};
const writer=new Writer();
await writer.removeMessageByUuid("message");
assert.equal(writer.queuedToolResults.size,0);
assert.deepEqual(events.splice(0),[["restart","file",true],["enqueue","file","message",undefined]]);
await writer.performRemoveByUuid("file","message",undefined,"queued-session");
assert.deepEqual(events.splice(0),[["archive","file","queued-session","message"]]);
fail=true;
await writer.performRemoveByUuid("file","message");
assert.deepEqual(events.splice(0),[["archive","file","session","message"],"purge"]);
disk=true;
await writer.performRemoveByUuid("file","message");
assert.deepEqual(events.splice(0),[]);
const remote={};
await writer.performRemoveByUuid("file","message",remote);
assert.deepEqual(events.splice(0),[["v5",remote,"key:file","message"]]);
''',
    )


def test_retraction_301_sdk_capture_binds_renamed_closure(tmp_path: Path) -> None:
    patched = patch_retractions(_SOURCE)
    assert 'remove(uuid,s,n.findLast(' in patched
    assert 'remove(uuid,s)}' not in patched
    sdk = _between(patched, 'function sdk(', '\nasync removeMessageByUuid(')
    _node(
        tmp_path,
        'retraction-301-sdk.mjs',
        '''
const calls=[];
function remove(...args){calls.push(args)}
const metric=()=>{},safe=x=>x;
'''
        + sdk
        + '''
const row={type:"assistant",uuid:"a"};
const messages=[row],conversation=[row],storage={v5:true};
const persisted=sdk({conversation,messages,persistSession:true,storageV5:storage});
persisted.advance();persisted.evict("a");
assert.equal(calls.length,1);
const [uuid,passedStorage,message,rows,persist]=calls[0];
assert.equal(uuid,"a");assert.equal(passedStorage,storage);assert.equal(message,row);
assert.equal(rows,messages);assert.equal(persist,true);
assert.equal(messages.length,0);assert.equal(conversation.length,0);
calls.length=0;
const memory=[{type:"user",uuid:"b"}];
sdk({conversation:[],messages:memory,persistSession:false,storageV5:undefined}).evict("b");
assert.equal(calls[0][4],false);
''',
    )


def test_retraction_301_rejects_unbound_sdk_closure() -> None:
    with pytest.raises(RetractionPatchError, match='SDK capture before eviction'):
        patch_retractions(
            _SOURCE.replace(
                '{conversation:e,messages:n,persistSession:r,storageV5:s}', 'opts'
            )
        )


@pytest.fixture(scope='module')
def native() -> tuple[str, str]:
    path = os.environ.get('CCPATCH_301_SOURCE')
    if not path:
        pytest.skip('set CCPATCH_301_SOURCE to pristine 2.1.301 source')
    source = Path(path).read_text()
    assert 'VERSION:"2.1.301"' in source
    return source, patch_retractions(source)


def test_native_retraction_301(native: tuple[str, str]) -> None:
    _, patched = native
    assert (
        'this.restartReplayIndex(r,!0),this.queuedToolResults.clear(),this.enqueueRemove(r,e,n)'
        in patched
    )
    assert (
        'let s=r!==void 0?ww(e):void 0;if(r===void 0&&!ce())try{await __ccpatchRetractionStorage.replace'
        in patched
    )
    assert 'lJt(ve,s,n.findLast((_ccRow)=>_ccRow.uuid===ve)??' in patched
    assert (
        'e.findLast((_ccRow)=>_ccRow.uuid===ve),n,r);let Ie=n.findLastIndex(' in patched
    )
    with pytest.raises(RetractionPatchError, match='missing transcript removal'):
        patch_retractions(patched)


def test_native_301_compactor_keeps_archives(
    native: tuple[str, str], tmp_path: Path
) -> None:
    source, _ = native
    emit = source.index('function TLe(e){return(n,r)=>')
    compact = source[source.rindex('async function wLe(', 0, emit) : emit]
    emitter = _between(source[emit:], 'function TLe(', 'function vLe(')
    start = source.rindex('var Mto={', 0, emit)
    policies = source[start : source.index('function Ito(', start)]
    _node(
        tmp_path,
        'retraction-301-compactor.mjs',
        _RUNTIME
        + policies
        + compact
        + emitter
        + '''
function Ito(e){try{return JSON.parse(e)}catch{return}}
function Pto(e){return e?.type==="system"&&e.subtype==="compact_boundary"}
class Tae{add(){}torn(){}finish(){return new Map()}}
const Sae=()=>({safeParse:()=>({success:true})}),_=JSON.stringify;
const archive=createRetractionArchive();
const original={type:"assistant",uuid:"old",parentUuid:null,message:{role:"assistant",content:[{type:"text",text:"NEVER_SEND"}]}};
const record=archive.capture("session",original,[original]);
const boundary={type:"system",subtype:"compact_boundary",uuid:"boundary",parentUuid:null,compactMetadata:{}};
const current={type:"user",uuid:"current",parentUuid:"boundary",message:{role:"user",content:"hello"}};
const lines=[record,original,boundary,current].map(JSON.stringify);
const result=await wLe(lines);
assert.equal(result.kind,"plan");
const write=TLe(result.plan);
const kept=lines.flatMap((line,index)=>write(index,line));
assert(kept.includes(JSON.stringify(record)));
assert(!kept.includes(JSON.stringify(original)));
''',
    )
