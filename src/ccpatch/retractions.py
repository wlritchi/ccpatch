"""Archive retracted messages without retaining them in model context."""

from __future__ import annotations

import re
from pathlib import Path

from .module_runtime import register_module_bootstrap

_RUNTIME = Path(__file__).with_name('retraction_runtime.js').read_text()
_STORAGE = Path(__file__).with_name('retraction_storage.js').read_text()
_ARCHIVE = 'globalThis.__ccpatchRuntime.retractions'
_ID = r'[\w$]+'


class RetractionPatchError(RuntimeError):
    """An upstream retraction anchor changed."""


def _replace(source: str, pattern: str, replacement: str, name: str) -> str:
    compiled = re.compile(pattern)
    matches = list(compiled.finditer(source))
    if len(matches) != 1:
        raise RetractionPatchError(f'{name}: expected one match, got {len(matches)}')
    return compiled.sub(replacement, source)


def patch_retractions(source: str) -> str:
    """Patch the verified 2.1.274 persistence and eviction paths."""
    remove = re.search(
        rf'async function (?P<remove>{_ID})\(e,n\)\{{let r=(?P<flag>{_ID})\(\)&&n!==void 0\?n:void 0;await (?P<writer>{_ID})\(\)\.removeMessageByUuid\(e,r\)\}}',
        source,
    )
    if remove is None:
        raise RetractionPatchError('missing transcript removal entry point')
    writer = remove['writer']
    session = re.search(
        rf'async function ({_ID})\(e,n\)\{{let r={remove["flag"]}\(\)&&n!==void 0\?n:void 0;if\(!\(await {_ID}\(\)\.sessionMessages\((?P<session>{_ID})\(\),r\)\)\.has\(e\)\)return;await {writer}\(\)\.removeMessageByUuid\(e,r\)\}}',
        source,
    )
    if session is None:
        raise RetractionPatchError('missing continuation removal entry point')
    sid = session['session']
    source = _replace(
        source,
        re.escape(session[0]),
        f'async function {session[1]}(e,n,m,h){{return {remove["remove"]}(e,n,m,h)}}',
        'continuation archive',
    )
    source = _replace(
        source,
        re.escape(remove[0]),
        f'async function {remove["remove"]}(e,n,m,h,s=!0){{'
        f'try{{{_ARCHIVE}.capture({sid}(),m,h)}}catch{{console.error("ccpatch: retraction snapshot failed; retaining native purge")}}'
        f'let r={remove["flag"]}()&&n!==void 0?n:void 0;'
        f'if(s)await {writer}().removeMessageByUuid(e,r)}}',
        'capture before persistence eviction',
    )
    source = _replace(
        source,
        rf'onTombstone:\((?P<msg>{_ID})\)=>\{{if\((?P<apply>{_ID})\(\{{type:"remove-by-uuid",uuid:(?P=msg)\.uuid\}}\),this\.stream\.transcriptRetractedUuids\.delete\((?P=msg)\.uuid\)\)return;(?P<remove>{_ID})\((?P=msg)\.uuid,(?P<storage>{_ID})\)\}}',
        r'onTombstone:(\g<msg>)=>{\g<remove>(\g<msg>.uuid,\g<storage>,\g<msg>,this._requireHost().transcript.getSnapshot());\g<apply>({type:"remove-by-uuid",uuid:\g<msg>.uuid});this.stream.transcriptRetractedUuids.delete(\g<msg>.uuid)}',
        'UI archive capture',
    )
    source = _replace(
        source,
        rf'for\(let (?P<uuid>{_ID}) of (?P<ids>{_ID})\)(?P<apply>{_ID})\(\{{type:"remove-by-uuid",uuid:(?P=uuid)\}}\),this\.stream\.transcriptRetractedUuids\.add\((?P=uuid)\),(?P<remove>{_ID})\((?P=uuid),(?P<storage>{_ID})\)',
        r'for(let \g<uuid> of \g<ids>){let _ccRows=this._requireHost().transcript.getSnapshot();\g<remove>(\g<uuid>,\g<storage>,_ccRows.find((_ccRow)=>_ccRow.uuid===\g<uuid>),_ccRows);\g<apply>({type:"remove-by-uuid",uuid:\g<uuid>}),this.stream.transcriptRetractedUuids.add(\g<uuid>)}',
        'continuation capture before UI eviction',
    )
    source = _replace(
        source,
        r'function O\(X\)\{let ye=r\.findLastIndex\(\(U\)=>U\.uuid===X\);',
        f'function O(X){{{remove["remove"]}(X,s,r.findLast((U)=>U.uuid===X)??e.findLast((U)=>U.uuid===X),r,n);let ye=r.findLastIndex((U)=>U.uuid===X);',
        'SDK capture before eviction',
    )
    source = _replace(
        source,
        rf'(i\("tengu_tombstone_persisted_removal",\{{message_type:{_ID}\(U.type\)\}}\)),{remove["remove"]}\(X,s\)',
        r'\1',
        'SDK duplicate removal',
    )
    source = _replace(
        source,
        r'async removeMessageByUuid\(e,n\)\{return this\.trackWrite\(async\(\)=>\{let r=this\.sessionFile;if\(r===null\)return;return this\.enqueueRemove\(r,e,n\)\}\)\}',
        f'async removeMessageByUuid(e,n){{if(this.shouldSkipPersistence())return;return this.trackWrite(async()=>{{let r=this.sessionFile;if(r===null){{let _ccEntry={_ARCHIVE}.pending(this.store.getSessionId(),e);if(_ccEntry){{this.pendingEntries=this.pendingEntries.filter((_ccPending)=>_ccPending.entry.uuid!==e&&_ccPending.entry.originalUuid!==e);if(n===void 0)this.pendingEntries.push({{entry:_ccEntry,storageV5:n}});else console.error("ccpatch: V5 disk archival unsupported; retaining memory copy")}}return}}return this.enqueueRemove(r,e,n)}})}}',
        'pending transcript archive',
    )
    source = _replace(
        source,
        r'\{removeUuid:n,resolve:s,storageV5:r\}',
        '{removeUuid:n,resolve:s,storageV5:r,ccpatchSessionId:this.store.getSessionId()}',
        'queued archive session identity',
    )
    source = _replace(
        source,
        r'this.performRemoveByUuid\(e,B.removeUuid,B.storageV5\)',
        'this.performRemoveByUuid(e,B.removeUuid,B.storageV5,B.ccpatchSessionId)',
        'queued archive session dispatch',
    )
    source = _replace(
        source,
        r'async performRemoveByUuid\(e,n,r\)\{let s=r!==void 0\?(?P<key>[\w$]+)\(e\):void 0;',
        'async performRemoveByUuid(e,n,r,_ccSession=this.store.getSessionId()){let s=r!==void 0?\\g<key>(e):void 0;if(r===void 0)try{await __ccpatchRetractionStorage.replace(e,_ccSession,n);return}catch(_ccError){console.error("ccpatch: retraction archive write failed; retaining native purge",_ccError.message)}else console.error("ccpatch: V5 disk archival unsupported; retaining memory copy");',
        'serialized archive conversion',
    )
    source = _replace(
        source,
        r'async appendEntry\(e,n=this.store.getSessionId\(\),r,s,m,h\)\{',
        f'async appendEntry(e,n=this.store.getSessionId(),r,s,m,h){{if(e.uuid&&{_ARCHIVE}.pending(n,e.uuid))return;if(e.type==={_ARCHIVE}.recordType&&s!==void 0)return;',
        'late write archive conversion',
    )
    source = _replace(
        source,
        r'let D=await this\.store\.sessionMessages\(n,s\),B=e\.isSidechain',
        f'let D=await this.store.sessionMessages(n,s);if(e.uuid&&{_ARCHIVE}.pending(n,e.uuid))return;let B=e.isSidechain',
        'post-await retraction guard',
    )
    source = _replace(
        source,
        r'switch\(G5r\[e.type\]\)\{',
        f'if(e.uuid&&{_ARCHIVE}.pending(n,e.uuid))return;switch(G5r[e.type]){{',
        'resolved session retraction guard',
    )
    source = _replace(
        source,
        r'var G5r=\{user:"dedup-transcript",',
        'var G5r={"ccpatch-retracted":"always",user:"dedup-transcript",',
        'archive local write policy',
    )
    source = _replace(
        source,
        r'fireMirror\(e,n\)\{for\(let r of this.mirrors\)',
        f'fireMirror(e,n){{n=n.filter((_ccEntry)=>_ccEntry.type!=={_ARCHIVE}.recordType);if(n.length===0)return;for(let r of this.mirrors)',
        'archive mirror exclusion',
    )
    resume = re.search(
        r'if\(y\)\{if\(JV\(y\)\)y=await Ure\(y,\{onTranscriptUnreadable:\(At\)=>\{U=At\},storageV5:r.storageV5\}\);',
        source,
    )
    if resume is None:
        raise RetractionPatchError('missing resolved transcript resume hook')
    key = re.search(
        r'async performRemoveByUuid\(e,n,r,_ccSession=this.store.getSessionId\(\)\)\{let s=r!==void 0\?([\w$]+)\(e\)',
        source,
    )
    assert key is not None
    source = _replace(
        source,
        re.escape(resume[0]),
        resume[0]
        + f'if(y.fullPath)try{{await __ccpatchRetractionStorage.restore(y.fullPath,{remove["flag"]}()?r.storageV5:void 0,{key[1]}(y.fullPath))}}catch{{console.error("ccpatch: retraction archive restore failed")}}',
        'resume full archive scan',
    )
    anchor = re.search(r'async performRemoveByUuid\(', source)
    assert anchor is not None
    boundary = source.rfind('/* ccpatch-module:', 0, anchor.start())
    insertion = source.find('*/', boundary) + 2 if boundary >= 0 else 0
    native = (
        '\nimport * as __ccpatchRetractionFs from "node:fs/promises";'
        '\nimport {createReadStream as __ccpatchRetractionReadStream,statSync as __ccpatchRetractionStatSync,renameSync as __ccpatchRetractionRenameSync} from "node:fs";'
        '\nimport {createInterface as __ccpatchRetractionReadline} from "node:readline";'
        f'\n{_STORAGE}\n'
        'const __ccpatchRetractionStorage=createRetractionStorage('
        '__ccpatchRetractionFs,__ccpatchRetractionReadStream,'
        f'__ccpatchRetractionReadline,{_ARCHIVE},{{statSync:__ccpatchRetractionStatSync,renameSync:__ccpatchRetractionRenameSync}});\n'
    )
    source = source[:insertion] + native + source[insertion:]
    return register_module_bootstrap(
        source, 'retractions', _RUNTIME + '\nreturn createRetractionArchive();'
    )
