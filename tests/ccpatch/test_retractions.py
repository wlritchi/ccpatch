"""Behavior and native compatibility checks for retraction archives."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch import retractions
from ccpatch.module_runtime import source_modules
from ccpatch.patches import RETRACTION_ARCHIVE, PatchError, default_patch_sets
from ccpatch.retractions import _RUNTIME, RetractionPatchError, patch_retractions

ROOT = Path(__file__).resolve().parents[2]


def _node(tmp_path: Path, source: str) -> None:
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is unavailable')
    script = tmp_path / 'archive.mjs'
    script.write_text('import assert from "node:assert/strict";\n' + source)
    result = subprocess.run(  # noqa: S603 - Run local regression code.
        [node, str(script)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def _between(source: str, start: str, end: str) -> str:
    assert source.count(start) == 1
    offset = source.index(start)
    return source[offset : source.index(end, offset + len(start))]


def test_version_gate() -> None:
    assert RETRACTION_ARCHIVE in default_patch_sets((2, 1, 274))
    for version in (None, (2, 1, 273), (2, 1, 275)):
        assert not RETRACTION_ARCHIVE.applies_to(version)
        assert RETRACTION_ARCHIVE not in default_patch_sets(version)
    with pytest.raises(PatchError, match='missing transcript removal'):
        RETRACTION_ARCHIVE.apply('unrecognized upstream source')


def test_runtime_and_storage() -> None:
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is unavailable')
    result = subprocess.run(  # noqa: S603 - Run checked-in regression code.
        [
            node,
            str(ROOT / 'tests/ccpatch/retraction_archive_regression.mjs'),
            str(Path(retractions.__file__).with_name('retraction_runtime.js')),
            str(Path(retractions.__file__).with_name('retraction_storage.js')),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope='module')
def native() -> tuple[str, str]:
    path = os.environ.get('CCPATCH_RETRACTION_SOURCE')
    if not path:
        pytest.skip('set CCPATCH_RETRACTION_SOURCE to pristine 2.1.274 source')
    source = Path(path).read_text()
    assert 'VERSION:"2.1.274"' in source
    return source, patch_retractions(source)


def test_native_anchors_fail_closed(native: tuple[str, str]) -> None:
    source, patched = native
    with pytest.raises(RetractionPatchError, match='missing transcript removal'):
        patch_retractions(source.replace('removeMessageByUuid', 'removedUpstream'))
    with pytest.raises(RetractionPatchError, match='missing transcript removal'):
        patch_retractions(patched)
    assert 'ccpatchSessionId:this.store.getSessionId()' in patched
    assert (
        'if(e.uuid&&globalThis.__ccpatchRuntime.retractions.pending(n,e.uuid))return;'
        in patched
    )
    assert 'if(s)await ki().removeMessageByUuid(e,r)' in patched


def test_unpatched_loader_and_compactor(
    native: tuple[str, str], tmp_path: Path
) -> None:
    original, _ = native
    module = next(
        item.source
        for item in source_modules(original)
        if 'function Lcr(e){let n=new Map' in item.source
    )
    loader = _between(module, 'function Lcr(e){let n=new Map', 'async function Cde(')
    compact = _between(module, 'async function Lwt(', 'function Fwt(')
    classify = _between(module, 'function sMs(', 'var AHe=')
    policies = _between(module, 'var oMs=', 'function sMs(')
    _node(
        tmp_path,
        _RUNTIME
        + loader
        + policies
        + classify
        + compact
        + '''
const archive=createRetractionArchive();
const original={type:"assistant",uuid:"old",parentUuid:null,message:{role:"assistant",content:[{type:"text",text:"NEVER_SEND"}]}};
const record=archive.capture("session",original,[original]);
const boundary={type:"system",subtype:"compact_boundary",uuid:"boundary",parentUuid:null,compactMetadata:{}};
const current={type:"user",uuid:"current",parentUuid:"boundary",message:{role:"user",content:"hello"}};
const Plr=x=>x!==null&&typeof x==="object", rEt=()=>false;
const zT=x=>["user","assistant","attachment","system"].includes(x.type);
const ule=()=>{}, $Os=()=>false, ia=x=>x.subtype==="compact_boundary", iOs=()=>false;
const i=()=>{}, Jo=JSON.parse, w=JSON.stringify, rMs=ia;
class IHe{finish(){return new Map()}}
const loader=Lcr(false);
loader.processEntry(record);loader.processEntry(boundary);loader.processEntry(current);
const loaded=loader.finish();
assert(!JSON.stringify([...loaded.messages.values()]).includes("NEVER_SEND"));
assert.equal(loaded.messages.has("old"),false);
const lines=[record,boundary,current].map(JSON.stringify);
const plan=await Lwt(lines);
assert.equal(plan.kind,"plan");
const emit=Nwt(plan.plan);
const kept=lines.flatMap((line,index)=>emit(index,line));
assert(kept.includes(JSON.stringify(record)));
assert(!JSON.stringify(record).includes('"uuid":"old"'));
''',
    )


def test_native_local_only_writer(native: tuple[str, str], tmp_path: Path) -> None:
    _, patched = native
    mirror = _between(patched, 'fireMirror(e,n){', 'incrementPendingWrites()')
    append = _between(
        patched,
        'async appendEntry(e,n=this.store.getSessionId(),r,s,m,h){',
        'beginTranscriptRelocation()',
    )
    remove = _between(
        patched, 'async removeMessageByUuid(e,n){', 'async performRemoveByUuid('
    )
    policies = _between(patched, 'var G5r=', ',Oar=') + ';'
    _node(
        tmp_path,
        _RUNTIME
        + policies
        + 'class Writer{'
        + mirror
        + append
        + remove
        + '''
store={getSessionId:()=>"s"};sessionFile="file";mirrors=[];pendingEntries=[];
shouldSkipPersistence(){return false}trackWrite(fn){return fn()}
enqueueWrite(path,entry){this.writes.push(entry);this.fireMirror(path,[entry])}
writes=[];
}
const archive=createRetractionArchive();globalThis.__ccpatchRuntime={retractions:archive};
const zT=x=>["assistant","user","system","attachment"].includes(x.type), Qve=()=>false;
const NHe=()=>undefined,wOs=()=>undefined;
const row={type:"assistant",uuid:"a",message:{content:[{type:"text",text:"SECRET"}]}};
const record=archive.capture("s",row,[row]);
const writer=new Writer();const mirrored=[];writer.mirrors.push((path,entries)=>mirrored.push(...entries));
await writer.appendEntry(record);
assert.equal(writer.writes.length,1);assert.equal(mirrored.length,0);
await writer.appendEntry(row);
await writer.appendEntry(record,undefined,undefined,{remote:true});
assert.equal(writer.writes.length,1);
writer.sessionFile=null;writer.pendingEntries=[{entry:row}];
await writer.removeMessageByUuid("a");await writer.removeMessageByUuid("a");
assert.equal(writer.pendingEntries.length,1);
assert.equal(writer.pendingEntries[0].entry.type,"ccpatch-retracted");
writer.pendingEntries=[{entry:row,storageV5:{remote:true}}];
await writer.removeMessageByUuid("a",{remote:true});
assert.equal(writer.pendingEntries.length,0);

// Retract while the native deduplication await is suspended.
const pendingRow={type:"assistant",uuid:"late",message:{content:[{type:"text",text:"LATE_SECRET"}]}};
const lateWriter=new Writer();
let resolveMessages;
const messages=new Promise(resolve=>{resolveMessages=resolve});
lateWriter.store.sessionMessages=()=>messages;
lateWriter.persistToRemote=async()=>{throw Error("retracted message uploaded")};
const inFlight=lateWriter.appendEntry(pendingRow);
archive.capture("s",pendingRow,[pendingRow]);
resolveMessages(new Set());
await inFlight;
assert.equal(lateWriter.writes.length,0);
''',
    )


def test_native_v5_uses_purge_only(native: tuple[str, str], tmp_path: Path) -> None:
    _, patched = native
    remove = _between(patched, 'async performRemoveByUuid(', 'async removeByUuidV5(')
    _node(
        tmp_path,
        'class Writer{'
        + remove
        + '''
store={getSessionId:()=>"s"};
async removeByUuidV5(backend,key,uuid){calls.push({backend,key,uuid});}
}
const calls=[];
const Lv=()=>"key",F=()=>true;
const __ccpatchRetractionStorage={replace:()=>{throw Error("archive must not be called")}};
const backend={remote:true};await new Writer().performRemoveByUuid("path","old",backend);
assert.deepEqual(calls,[{backend,key:"key",uuid:"old"}]);
''',
    )


def test_native_sdk_eviction(native: tuple[str, str], tmp_path: Path) -> None:
    _, patched = native
    tracker = _between(
        patched,
        'function Ff({conversation:e,messages:r,persistSession:n,storageV5:s})',
        'function xf(',
    )
    _node(
        tmp_path,
        _RUNTIME
        + tracker
        + '''
const archive=createRetractionArchive(), calls=[];
function Fat(uuid,storage,message,rows,persist=true){archive.capture("s",message,rows);if(persist)calls.push(uuid)}
const i=()=>{}, c=x=>x;
for(const persist of [false,true]){
 const message={type:"assistant",uuid:String(persist),message:{content:[{type:"thinking",thinking:"SECRET"}]}};
 const conversation=[message], messages=[message];
 const tracker=Ff({conversation,messages,persistSession:persist});
 tracker.evict(message.uuid);
 assert.equal(conversation.length,0);assert.equal(messages.length,0);
 assert(archive.snapshot("s").entries.some(x=>x.originalUuid===message.uuid));
}
assert.deepEqual(calls,["true"]);
''',
    )


def test_native_ui_and_continuation_capture(
    native: tuple[str, str], tmp_path: Path
) -> None:
    _, patched = native
    tombstone = _between(patched, 'onTombstone:(mt)=>{Fat(', ',onRefusalContinuation:')
    continuation = _between(
        patched, 'for(let Je of Oe){let _ccRows=', '}xe.setSalvage(null)'
    )
    assert continuation.endswith('}}')
    continuation = continuation[:-1]
    _node(
        tmp_path,
        _RUNTIME
        + '''
const archive=createRetractionArchive();
let rows=[{type:"user",uuid:"u"},{type:"assistant",uuid:"a",message:{content:[{type:"text",text:"SECRET"}]}}];
const calls=[];const Ne={},Se={};
function Fat(uuid,storage,message,snapshot){archive.capture("s",message,snapshot);calls.push(uuid)}
const vBn=Fat;
const Oe=action=>{rows=rows.filter(row=>row.uuid!==action.uuid)};
const host={stream:{transcriptRetractedUuids:new Set()},_requireHost:()=>({transcript:{getSnapshot:()=>rows}})};
const factory=function(){return {'''
        + tombstone
        + '''}};
factory.call(host).onTombstone(rows[1]);
assert.deepEqual(rows.map(x=>x.uuid),["u"]);
assert.equal(archive.snapshot("s").entries[0].placement.previousUuid,"u");
rows.push({type:"assistant",uuid:"b",message:{content:[{type:"thinking",thinking:"SECRET_THINKING"}]}});
(function(){let Oe=["b"],Ce=action=>{rows=rows.filter(row=>row.uuid!==action.uuid)};'''
        + continuation
        + '''}).call(host);
assert.equal(rows.length,1);assert.deepEqual(calls,["a","b"]);
assert.equal(archive.snapshot("s").entries.length,2);
''',
    )
