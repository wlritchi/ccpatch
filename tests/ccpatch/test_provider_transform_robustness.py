"""Check exact edits inside matched provider fragments."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from ccpatch.patches import (
    _PROVIDER_ENV_SNAPSHOT,
    BACKGROUND_PROVIDER_ENV,
    PatchError,
    _attribution_function,
    _replace_provider_snapshot,
)

_CASES = [
    (
        "add-control-provider-env",
        'z.object({proto:p,op:z.literal("dispatch"),d:job(),timeoutMs:z.number(),auth:',
        'timeoutMs:',
    ),
    (
        "acknowledge-provider-env-version",
        'return respond(socket,{ok:!0,op:operation,short:id,pid:worker.record.pid,',
        'op:operation,',
    ),
    (
        "declare-worker-provider-env",
        'Worker{dispatch;spawnPty;getAuthSnapshot;via;record;',
        'dispatch;',
    ),
    (
        "thread-provider-env-through-spawn",
        'static spawn(job,spawn,auth,options){let worker=new Worker(job,spawn??fallback(),auth,"cold");',
        '"cold")',
    ),
    (
        "apply-provider-env-to-claim-frame",
        'static buildClaimFrame(job,snapshot,auth){let dir=jobDir(job.short),env=buildEnv(job,dir,snapshot,sock(job.short),auth);',
        'auth)',
    ),
    (
        "thread-provider-env-to-claim-frame",
        'function frame(job,snapshot,auth,claimAuth){let{env:e,argv:a}=Worker.buildClaimFrame(job,snapshot,auth);',
        'auth);',
    ),
    (
        "accept-transient-provider-env-in-manager",
        'dispatch=async(job,retry=0,afterUpgrade)=>{',
        'afterUpgrade)=>',
    ),
    (
        "remove-provider-env-from-state-writes",
        'async function write(dir,state){let{pinned:p,sortOrder:s,stateSortOrder:i,...rest}=state;',
        '...rest',
    ),
    (
        "scrub-worker-provider-env",
        'function build(job,dir,snapshot,sock,auth){let ambient={...process.env},env={...ambient,OTHER:"kept"};if(process.env.',
        'auth){',
    ),
]


@pytest.mark.parametrize(("name", "source", "anchor"), _CASES)
def test_provider_exact_edits_reject_drift(
    name: str, source: str, anchor: str, mocker: MockerFixture
) -> None:
    patch = next(
        patch for patch in BACKGROUND_PROVIDER_ENV.patches if patch.name == name
    )
    match = patch.pattern.fullmatch(source)
    assert match is not None
    assert not isinstance(patch.replacement, str)
    assert patch.replacement(match) != source

    for altered in (
        source.replace(anchor, "missingAnchor"),
        source.replace(anchor, anchor + anchor, 1),
    ):
        changed = mocker.MagicMock(spec=re.Match)

        def group(key: str | int, text: str = altered) -> str | None:
            return text if key == 0 else match.group(key)

        changed.group.side_effect = group
        with pytest.raises(PatchError, match="provider"):
            patch.replacement(changed)


def test_claim_frame_changes_signature_and_call_exactly_once() -> None:
    name, source, _ = _CASES[4]
    patch = next(
        patch for patch in BACKGROUND_PROVIDER_ENV.patches if patch.name == name
    )
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    assert patched == source.replace('auth)', 'auth,_ccProviderEnv)')
    assert patched.count('_ccProviderEnv') == 2


def test_claimed_entry_preserves_incidental_braces() -> None:
    source = (
        'async function entry(claim,main){let extra={nested:{keep:!0}};'
        'Object.assign(process.env,claim.env),process.argv=["node"],'
        'cache(),auth(),other();let{main:workerMain}=await main;await workerMain()}'
    )
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV.patches
        if patch.name == 'apply-provider-env-after-claimed-initializers'
    )
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    assert patched == source.replace(
        'entry(claim,main){',
        'entry(claim,main){_ccProviderWorkerEnv=_ccProviderCaptureTransport(claim.env);',
    ).replace('await workerMain()', '_ccProviderApplyWorkerFinal();await workerMain()')


_RV_GUARD = (
    'if(token&&!authenticated&&request.type!=="repaint"){'
    'send({type:"reply-rejected"});return}'
)
_RV_SHUTDOWN = 'if(request.type==="shutdown"){'
_RV_EXTRACTED = (
    'shutdown();return}'
    'if(request.type==="repaint"){repaint();return}'
    'if(request.type==="attacher-caps"){caps(request);return}'
    'if(request.type==="reply"&&typeof request.text==="string")reply(request)}'
    'function shutdown(){'
)
_RV_SEND = 'send({type:"shutting-down"});'
_RV_DISCOVERY = (
    'const metadata={sessionId:session(),gates:{}};'
    'const supervisor={proto:protocol,role:"supervisor",supervisorPid:1};'
    'function authenticate(request){if("auth"in request&&validate(request.auth,token))'
    'authenticated=!0;return}'
)


@pytest.mark.parametrize('extracted', [False, True], ids=['legacy', '207'])
def test_authenticated_transfer_preserves_dispatch_and_checks_auth(
    extracted: bool,
) -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV.patches
        if patch.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    fragment = (
        _RV_GUARD + _RV_SHUTDOWN + (_RV_EXTRACTED if extracted else '') + _RV_SEND
    )
    source = _RV_DISCOVERY + 'function dispatch(request){' + fragment + '}}'
    if extracted:
        source = source[:-1]
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    start = patched.index('if(request.type==="cc-provider-snapshot-request")')
    end = patched.index(_RV_SHUTDOWN, start)
    assert patched[:start] + patched[end:] == source
    assert patched.index(_RV_GUARD) < start < end
    assert patched.count('_ccReply.mac=_ccProviderMac(token,_ccReply)') == 1
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is not available')
    program = (
        'let token="secret",authenticated=true,protocol=7,outputs=[];'
        'const session=()=>"session",validate=(a,b)=>a===b,send=x=>outputs.push(x);'
        'const _ccProviderWorkerEnv={API_KEY:"private"},_ccProviderMac=()=>"mac";'
        + patched
        + ';const valid={type:"cc-provider-snapshot-request",auth:token,proto:7,'
        'version:3,sessionId:"session",nonce:"a".repeat(64)};'
        'dispatch(valid);if(outputs.length!==1||outputs[0].mac!=="mac"||'
        'outputs[0].payload.API_KEY!=="private")throw Error("missing snapshot");'
        'for(const invalid of [{auth:"bad"},{proto:8},{version:2},'
        '{sessionId:"other"},{nonce:"bad"}]){outputs=[];dispatch({...valid,...invalid});'
        'if(outputs.length)throw Error("invalid request leaked snapshot")}'
        'for(const state of [["secret",false],["",true],[null,true]]){'
        '[token,authenticated]=state;outputs=[];dispatch(valid);'
        'if(outputs.some(x=>x.type==="cc-provider-snapshot"))throw Error("auth bypass")}'
        'token="secret";authenticated=true;outputs=[];dispatch({type:"shutdown"});'
        'if(outputs[0].type!=="shutting-down")throw Error("shutdown changed");'
    )
    subprocess.run(  # noqa: S603 - Execute the synthetic request dispatcher.
        [node, '-e', program], check=True, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    'change',
    [
        ('function shutdown()', 'function unrelated()'),
        ('shutdown();return}', 'shutdown(request);return}'),
        ('type==="attacher-caps"', 'type==="unknown"'),
    ],
)
def test_authenticated_transfer_rejects_extracted_shutdown_drift(
    change: tuple[str, str],
) -> None:
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV.patches
        if patch.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    source = _RV_GUARD + _RV_SHUTDOWN + _RV_EXTRACTED + _RV_SEND
    assert len(list(patch.pattern.finditer(source))) == 1
    assert patch.pattern.search(source.replace(*change)) is None


@pytest.mark.parametrize('legacy', [False, True], ids=['modern', '200-202'])
@pytest.mark.parametrize('dollars', [False, True], ids=['plain', 'dollar-identifiers'])
def test_worker_auth_validator_discovery(legacy: bool, dollars: bool) -> None:
    patch = next(
        item
        for item in BACKGROUND_PROVIDER_ENV.patches
        if item.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    discovery = _RV_DISCOVERY
    if legacy:
        discovery = discovery.replace(
            'if("auth"in request&&validate(request.auth,token))authenticated=!0;',
            'if(token&&"auth"in request)if(validate(request.auth,token))'
            'authenticated=!0;else send({type:"auth-rejected"});',
        )
    source = (
        discovery
        + 'function dispatch(request){'
        + _RV_GUARD
        + _RV_SHUTDOWN
        + _RV_SEND
        + '}}'
    )
    if dollars:
        for name in ['request', 'validate', 'token', 'authenticated', 'send']:
            source = re.sub(rf'\b{name}\b', '$' + name + '$', source)
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    expected = (
        '$validate$($request$.auth,$token$)'
        if dollars
        else 'validate(request.auth,token)'
    )
    assert patched.count(expected) == 2


@pytest.mark.parametrize('other', ['validate', 'otherValidator'])
@pytest.mark.parametrize('legacy', [False, True])
def test_worker_auth_validator_ambiguous_discovery(other: str, legacy: bool) -> None:
    patch = next(
        item
        for item in BACKGROUND_PROVIDER_ENV.patches
        if item.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    extra = (
        f'if(token&&"auth"in other)if({other}(other.auth,token))authenticated=!0;'
        if legacy
        else f'if("auth"in other&&{other}(other.auth,token))authenticated=!0;'
    )
    source = (
        _RV_DISCOVERY
        + extra
        + 'function dispatch(request){'
        + _RV_GUARD
        + _RV_SHUTDOWN
        + _RV_SEND
        + '}}'
    )
    with pytest.raises(PatchError):
        patch.pattern.sub(patch.replacement, source)


@pytest.mark.parametrize('legacy', [False, True])
def test_worker_auth_validator_requires_same_request(legacy: bool) -> None:
    patch = next(
        item
        for item in BACKGROUND_PROVIDER_ENV.patches
        if item.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    discovery = _RV_DISCOVERY.replace(
        'validate(request.auth,token)', 'validate(other.auth,token)'
    )
    if legacy:
        discovery = discovery.replace(
            '"auth"in request&&', 'token&&"auth"in request)if('
        )
    source = (
        discovery
        + 'function dispatch(request){'
        + _RV_GUARD
        + _RV_SHUTDOWN
        + _RV_SEND
        + '}}'
    )
    with pytest.raises(PatchError):
        patch.pattern.sub(patch.replacement, source)


@pytest.mark.parametrize('version', [200, 201, 202, 203])
def test_captured_worker_auth_security(version: int) -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / f'build/sweep-resume/2.1.{version}/linux-x64/original.js'
    )
    if not path.is_file():
        pytest.skip(f'captured pristine .{version} source is unavailable')
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is not available')
    source = path.read_text()
    patch = next(
        item
        for item in BACKGROUND_PROVIDER_ENV.patches
        if item.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    matches = list(patch.pattern.finditer(source))
    assert len(matches) == 1
    match = matches[0]
    assert not isinstance(patch.replacement, str)
    replacement = patch.replacement(match)
    dispatcher = {200: 'bWf', 201: 'bWf', 202: 'Q9f', 203: 'TEy'}[version]
    start = source.index(f'function {dispatcher}(')
    end = source.index('async function ', start)
    native = source[start:end]
    assert native.endswith('}}')
    assert native.count(match.group(0)) == 1
    patched = native.replace(match.group(0), replacement)
    validator_name = {200: 'Vie', 201: 'Vie', 202: 'tae', 203: 'gTe'}[version]
    # Restrict the search to the auth body; .202 reuses the name in another module.
    validators = re.findall(
        rf'function {validator_name}\(e,t\)\{{if\(typeof e!=="string"[\s\S]*?timingSafeEqual\([^)]*\)\}}',
        source,
    )
    assert len(validators) == 1
    validator = validators[0]
    crypto = re.search(r'return ([\w$]+)\.timingSafeEqual', validator)
    assert crypto is not None
    session = re.search(r'sessionId:([\w$]+)\(\),gates:\{', source)
    protocol = re.search(r'proto:([\w$]+),role:"supervisor",supervisorPid:', source)
    parser = re.search(r'let t;try\{t=([\w$]+)\(e\)', native)
    assert session is not None and protocol is not None and parser is not None
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(source)
    assert snapshot is not None
    helpers = _replace_provider_snapshot(snapshot)
    mac = _attribution_function(helpers, '_ccProviderMac')
    token, authenticated, send = (
        match.group(key) for key in ['token', 'authenticated', 'send']
    )
    stubs = (
        'const v=()=>{};'
        if version < 202
        else 'const T=()=>{};'
        if version == 202
        else 'let Onr=false;const w=()=>{},Owd=()=>{},He=()=>{};'
    )
    program = (
        'const assert=require("node:assert/strict"),crypto=require("node:crypto");'
        f'const {crypto.group(1)}=crypto,{parser.group(1)}=JSON.parse;'
        f'const {session.group(1)}=()=>"session",{protocol.group(1)}=7;'
        f'let {token}="secret",{authenticated}=false,outputs=[];'
        f'const {send}=x=>outputs.push(x),_ccProviderWorkerEnv={{ANTHROPIC_API_KEY:"private"}};'
        + stubs
        + validator
        + mac
        + patched
        + f';const dispatch=x=>{dispatcher}(JSON.stringify(x));'
        'const valid={type:"cc-provider-snapshot-request",auth:"secret",proto:7,version:3,'
        'sessionId:"session",nonce:"a".repeat(64)};'
        'const snapshots=()=>outputs.filter(x=>x.type==="cc-provider-snapshot");'
        'dispatch(valid);assert.equal(snapshots().length,0);'
        'outputs=[];dispatch({role:"supervisor",auth:"wrong!"});'
        'assert.deepEqual(outputs,[{type:"auth-rejected"}]);'
        f'assert.equal({authenticated},false);dispatch(valid);assert.equal(snapshots().length,0);'
        'dispatch({role:"supervisor",auth:"secret"});'
        f'assert.equal({authenticated},true);'
        'for(const invalid of [{auth:"wrong!"},{auth:"short"},{auth:null},{auth:42},{auth:""},'
        '{proto:8},{version:2},{sessionId:"other"},{nonce:"bad"},{nonce:null},'
        '{nonce:"A".repeat(64)},{nonce:"a".repeat(63)},{nonce:"a".repeat(65)}]){'
        'outputs=[];dispatch({...valid,...invalid});assert.deepEqual(outputs,[],JSON.stringify(invalid));}'
        'outputs=[];dispatch(valid);assert.equal(outputs.length,1);'
        'const expected={type:"cc-provider-snapshot",proto:7,version:3,sessionId:"session",'
        'nonce:valid.nonce,payload:{ANTHROPIC_API_KEY:"private"}};'
        'expected.mac=crypto.createHmac("sha256","secret").update(JSON.stringify(['
        'expected.type,7,3,"session",valid.nonce,expected.payload])).digest("hex");'
        'assert.deepEqual(outputs,[expected]);'
        f'for(const missing of ["",null]){{{token}=missing;outputs=[];dispatch(valid);'
        'assert.deepEqual(outputs,[]);}'
    )
    result = subprocess.run(  # noqa: S603 - Run extracted native functions in isolation.
        [node, '-e', program], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    'platform', ['linux-x64', 'linux-arm64', 'darwin-x64', 'darwin-arm64']
)
def test_authenticated_transfer_207_platform_source(platform: str) -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / 'build/sweep-resume/2.1.207'
        / platform
        / 'original.js'
    )
    if not path.is_file():
        pytest.skip('local .207 source is not available')
    source = path.read_text()
    patch = next(
        patch
        for patch in BACKGROUND_PROVIDER_ENV.patches
        if patch.name == 'serve-authenticated-provider-snapshot-on-worker-rv'
    )
    matches = list(patch.pattern.finditer(source))
    assert len(matches) == 1
    match = matches[0]
    assert match.group('shutdown') is not None
    patched, count = patch.pattern.subn(patch.replacement, source)
    assert count == 1
    assert patched.count('type==="cc-provider-snapshot-request"') == 1
    assert patched.count('payload:_ccProviderWorkerEnv') == 1
    assert f'{match.group("send")}(_ccReply)' in patched
    assert (
        f'function {match.group("shutdown")}(){{{match.group("send")}({{type:"shutting-down"}});'
        in patched
    )
