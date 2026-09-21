"""Length-free JavaScript patches applied to the extracted ``cli.js`` source.

Unlike whole-binary byte patching, these run on the decoded source string and
may change its length freely -- the container repack absorbs the size change.
Each :class:`PatchSet` is version-gated and self-verifying: after applying, it
asserts that expected markers appear (and forbidden ones do not), so a silent
no-op on a restructured future build fails loudly instead of shipping a binary
that looks patched but isn't.

Patterns match minified identifiers structurally (``[\\w$]+`` capture/backref),
pinning only stable string and property literals -- resilient to reminification.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import NotRequired, TypedDict
from typing import override as typing_override

from .agents_handoff import agents_view_handoff
from .module_runtime import (
    ModuleRuntimeError,
    ensure_module_reference,
    module_reference,
    register_module_bootstrap,
    source_modules,
)

Version = tuple[int, ...]


class ModelCosts(TypedDict):
    inputTokens: float
    outputTokens: float
    promptCacheWriteTokens: float
    promptCacheReadTokens: float
    webSearchRequests: float


ModelCostsByModel = dict[str, ModelCosts]


class MultiProviderModel(TypedDict):
    wireModel: str
    label: str
    description: str
    contextWindow: int
    maxOutputTokens: int
    costs: ModelCosts


class MultiProviderDefinition(TypedDict):
    provider: str
    attributionDomain: str
    baseURL: str
    tokenEnv: str
    availabilityEnv: NotRequired[str]
    baseURLEnv: NotRequired[str]
    defaultHeaders: dict[str, str]
    models: tuple[MultiProviderModel, ...]


class PatchError(RuntimeError):
    """A patch failed to match, or post-apply verification failed."""


def checked_replace(
    source: str, old: str, new: str, *, context: str, count: int = 1
) -> str:
    """Replace an exact fragment only when its cardinality is known."""
    actual = source.count(old)
    if not old or actual != count:
        raise PatchError(
            f"{context}: expected {count} exact occurrences of {old!r}, got {actual}"
        )
    return source.replace(old, new)


def parse_version(text: str) -> Version:
    """``"2.1.170"`` -> ``(2, 1, 170)``; a trailing suffix like ``-beta.1`` is dropped."""
    match = re.match(r"\d+(?:\.\d+)*", text)
    return tuple(int(p) for p in match.group(0).split(".")) if match else ()


@dataclass(frozen=True)
class Patch:
    name: str
    pattern: re.Pattern[str]
    replacement: str | Callable[[re.Match[str]], str]
    required: bool = True
    identifiers: tuple[re.Pattern[str], ...] = ()
    bound_replacement: Callable[[re.Match[str], dict[str, str]], str] | None = None
    expected_matches: tuple[int, ...] = (1,)

    def __post_init__(self) -> None:
        if not self.expected_matches or any(n < 1 for n in self.expected_matches):
            raise ValueError("expected_matches must contain positive cardinalities")


def discover_identifiers(
    source: str, patterns: tuple[re.Pattern[str], ...]
) -> dict[str, str]:
    """Resolve each semantic anchor once and reject conflicting bindings."""
    bindings: dict[str, str] = {}
    for pattern in patterns:
        matches = list(pattern.finditer(source))
        if len(matches) != 1:
            raise PatchError(
                f"identifier discovery: expected one match, got {len(matches)}: "
                f"{pattern.pattern!r}"
            )
        for name, value in matches[0].groupdict().items():
            if value is None:
                raise PatchError(f"identifier discovery: missing binding {name!r}")
            if name in bindings and bindings[name] != value:
                raise PatchError(f"identifier discovery: conflicting binding {name!r}")
            bindings[name] = value
    return bindings


@dataclass(frozen=True)
class PatchSet:
    name: str
    patches: tuple[Patch, ...]
    verify_present: tuple[re.Pattern[str], ...] = ()
    verify_absent: tuple[re.Pattern[str], ...] = ()
    min_version: Version | None = None  # inclusive
    max_version: Version | None = None  # exclusive
    requires_version: bool = False

    def applies_to(self, version: Version | None) -> bool:
        if version is None:
            return not self.requires_version
        if self.min_version is not None and version < self.min_version:
            return False
        if self.max_version is not None and version >= self.max_version:
            return False
        return True

    def apply(self, source: str) -> str:
        for patch in self.patches:
            context = f"{self.name}: patch {patch.name!r}"
            matches = list(patch.pattern.finditer(source))
            cardinalities = patch.expected_matches + (() if patch.required else (0,))
            if len(matches) not in cardinalities:
                detail = "matched nothing; " if not matches else ""
                raise PatchError(
                    f"{context} {detail}expected match count {cardinalities}, "
                    f"got {len(matches)}"
                )
            if not matches:
                continue
            try:
                bindings = discover_identifiers(source, patch.identifiers)
            except PatchError as exc:
                raise PatchError(f"{context}: {exc}") from exc
            replacement = patch.replacement
            if patch.bound_replacement is not None:
                bound = patch.bound_replacement

                def replace_bound(
                    match: re.Match[str],
                    bound: Callable[[re.Match[str], dict[str, str]], str] = bound,
                    bindings: dict[str, str] = bindings,
                    identifiers: tuple[re.Pattern[str], ...] = patch.identifiers,
                ) -> str:
                    qualified = dict(bindings)
                    for pattern in identifiers:
                        definition = pattern.search(match.string)
                        assert definition is not None
                        for name, value in definition.groupdict().items():
                            if value is not None and name != "entry":
                                qualified[name] = module_reference(
                                    match.string,
                                    definition.start(),
                                    value,
                                    match.start(),
                                )
                    return bound(match, qualified)

                replacement = replace_bound

            try:
                parts: list[str] = []
                offset = 0
                for match in matches:
                    parts.append(source[offset : match.start()])
                    parts.append(
                        match.expand(replacement)
                        if isinstance(replacement, str)
                        else replacement(match)
                    )
                    offset = match.end()
                parts.append(source[offset:])
                source = "".join(parts)
            except PatchError as exc:
                raise PatchError(f"{context}: {exc}") from exc
        for marker in self.verify_present:
            if marker.search(source) is None:
                raise PatchError(
                    f"{self.name}: expected marker absent after apply: {marker.pattern!r}"
                )
        for marker in self.verify_absent:
            if marker.search(source) is not None:
                raise PatchError(
                    f"{self.name}: forbidden marker still present: {marker.pattern!r}"
                )
        return source


# --- concrete patch sets ----------------------------------------------------

_ID = r"[\w$]+"
_V_2_1_151 = (2, 1, 151)
_V_2_1_174 = (2, 1, 174)

# Drop the early-return guard that hides the standalone thinking block, and force
# its render to the expanded (transcript + verbose) branch.
_THINKING_RENDER = (
    Patch(
        name="drop-thinking-early-return",
        pattern=re.compile(
            rf'(case"thinking":\{{(?:if\((?:{_ID}!==null&&{_ID}!==null&&)?{_ID}\({_ID}\)\)'
            rf'\{{[^\n]{{0,1000}}?return {_ID}\}})?)if\(!{_ID}(?:&&!{_ID})+\)'
            r'(?:return null;|\{return null;?\})'
        ),
        replacement=r"\1",
    ),
    Patch(
        name="force-transcript-and-verbose",
        pattern=re.compile(
            rf"({_ID}\({_ID},\{{addMargin:{_ID},param:{_ID},"
            rf"isTranscriptMode:){_ID}(,verbose:){_ID}"
        ),
        replacement=r"\1true\2true",
    ),
)

# 2.1.151+ folds thinking into the tool-use group; neutralize the extractor so
# the thinking message falls through to the standalone render path above.
_THINKING_UNGROUP = Patch(
    name="disable-thinking-grouping",
    pattern=re.compile(
        rf"(:null,{_ID}={_ID}===null\?)({_ID}\([\w$,]*\))"
        rf"(:void 0;if\({_ID}\)\{{{_ID}\.latestThinkingSummary)"
    ),
    replacement=r"\1void 0\3",
)


def thinking_expanded(version: Version | None) -> PatchSet:
    """Always render assistant thinking in full (pre- and post-2.1.151)."""
    patches = _THINKING_RENDER
    if version is None or version >= _V_2_1_151:
        patches = (*patches, _THINKING_UNGROUP)
    return PatchSet(
        name="thinking-expanded",
        patches=patches,
        verify_present=(re.compile(r"isTranscriptMode:true,verbose:true"),),
        verify_absent=(_THINKING_RENDER[0].pattern,),
    )


# --- enable channels (2.1.151+) ---------------------------------------------

CHANNELS_ENABLED = PatchSet(
    name="channels-enabled",
    patches=(
        # Force the "channels not enabled" subexpression false -> channels on.
        Patch(
            name="channelator",
            pattern=re.compile(rf"(?<![\w$.]){_ID}\?\.channelsEnabled!==!0"),
            replacement="!1",
        ),
        # Force the tengu_harbor feature flag on.
        Patch(
            name="channelizer",
            pattern=re.compile(rf'(?<![\w$.]){_ID}\("tengu_harbor",!1\)'),
            replacement="!!1",
        ),
    ),
    verify_absent=(
        re.compile(r"\?\.channelsEnabled!==!0"),
        re.compile(r'"tengu_harbor",!1'),
    ),
    min_version=_V_2_1_151,
)

# --- dev-channel inheritance for background agents (2.1.151+) ----------------
#
# >>> ACTIVATION CONTRACT -- read before changing dev-channel activation <<<
#
# ccpatch activates development channels by SCANNING THE DISPATCHING PROCESS'S
# OWN ARGV for --dangerously-load-development-channels (see _DISPATCH_DEV_CHANNELS
# below: it reads process.argv.slice(2) and reads NO environment variable). That
# "no env" property is load-bearing and enforced -- this PatchSet's verify_absent
# pins CLAUDE_DEV_CHANNELS to *absent*, so any change that re-introduces ambient
# env reading fails the patch by construction.
#
# Consequence for callers: a non-interactive launcher (bg-agent worker, daemon
# respawn, headless -p dispatch) MUST put --dangerously-load-development-channels
# on the LAUNCH ARGV. Exporting an env var alone does NOT activate channels --
# nothing here reads one, by design. A consumer that only has the value in an env
# var must translate env -> argv flag at the launch site.
#
# History/trap: an earlier shim read the channel list from the environment; that
# ambient-env path was dropped in favor of the native argv threading below. If
# activation ever breaks, fix it in the CONSUMER (make it pass the flag on argv)
# -- do NOT re-add env reading here; that reintroduces the exact regression class
# this verify_absent guard exists to stop.
#
# --dangerously-load-development-channels never reaches the bg-agent workers the
# agents view spawns. Three things drop it, all fixed natively here -- no env
# var, no wrapper:
#
#   1. Live dispatch: the worker argv (dispatchExtraArgs) is built by
#      $UH(HUH(cfg)), and $UH only serializes --settings/--plugin-dir/--add-dir/
#      --mcp-config/--strict-mcp-config -- it never carries channels. Append the
#      dev-channels (scanned from the dispatching process's own argv, which is
#      how the user passed them) so they land in dispatchExtraArgs -> the worker.
#   2. Persist/resume: those dispatch args double as the job's persisted
#      respawnFlags, filtered through the RfH (value-flag) + HE6 (multi-value)
#      allowlists on resume -- add the flag there (next to --channels) to survive.
#   3. Worker parse: dev channels need an interactive confirmation dialog (gated
#      on !isNonInteractiveSession) a bg worker can't show, so the worker skips
#      the parse. After that block, register the worker's OWN parsed specs for bg
#      sessions -- tagging each {dev:!0} (the allowlist bypass) and calling the
#      registrar directly (the parent already confirmed). Scoped to bg so other
#      non-interactive sessions (-p, etc.) still require the dialog.
#
# All length-free: extend two Set literals, append one array element, and append
# one statement after the parse block (captured verbatim). Minified identifiers
# are captured; only stable literals are pinned.

# Forward the flag through the bg-worker respawn allowlists, next to --channels.
_DEV_CHANNEL_FORWARD = (
    Patch(
        name="dev-channel-respawn-value-flag",
        pattern=re.compile(
            r'("--channels",(?:"--watch-artifact","--watch-artifact-no-autoreact",)?)'
            r'"--permission-prompt-tool"'
        ),
        replacement=r'\1"--dangerously-load-development-channels","--permission-prompt-tool"',
    ),
    Patch(
        name="dev-channel-respawn-multivalue",
        pattern=re.compile(re.escape('"--file","--channels"]')),
        replacement='"--file","--channels","--dangerously-load-development-channels"]',
    ),
)

# Append dev-channels to the dispatch serializer ($UH) return array, scanned from
# the dispatching process's own argv (variadic: collect bare tokens after the
# flag, stop at the next -flag; also accept the --flag=value form).
_DISPATCH_DEV_CHANNELS = (
    "(()=>{let _dc=[],_co=!1;for(let _ar of process.argv.slice(2)){"
    'if(_ar==="--dangerously-load-development-channels")_co=!0;'
    'else if(_ar.startsWith("--dangerously-load-development-channels="))'
    '_dc.push(_ar.slice(_ar.indexOf("=")+1));'
    'else if(_ar.startsWith("-"))_co=!1;'
    "else if(_co)_dc.push(_ar)}"
    'return _dc.length?["--dangerously-load-development-channels",..._dc]:[]})()'
)


def _dispatch_forward_replacement(m: re.Match[str]) -> str:
    # Insert before the closing `]` of the $UH return array, after the
    # --strict-mcp-config element (captured as group 1).
    channels = _DISPATCH_DEV_CHANNELS.replace(
        "--dangerously-load-development-channels", "--channels"
    )
    return f"{m.group(1)},...{_DISPATCH_DEV_CHANNELS},...{channels}{m.group(2)}]"


def _dev_channel_replacement(m: re.Match[str]) -> str:
    base, parse, register, dev_arg = (
        m.group(2),
        m.group(3),
        m.group(4),
        m.group(5),
    )
    # Brace-wrap so the appended statement ends in `}` -- the source continues
    # immediately with `if(...)`, and `n9H(...)if(` (no separator) is a syntax
    # error that the marker-only structural verify would not catch.
    return m.group(0) + (
        'if(process.env.CLAUDE_CODE_SESSION_KIND==="bg"&&'
        f"{dev_arg}&&{dev_arg}.length>0){{"
        f"{register}([...{base},...{parse}({dev_arg},"
        '"--dangerously-load-development-channels")'
        ".map((devEntry)=>({...devEntry,dev:!0}))])}"
    )


DEV_CHANNEL_INHERITANCE = PatchSet(
    name="dev-channel-inheritance",
    patches=(
        *_DEV_CHANNEL_FORWARD,
        Patch(
            name="dev-channel-dispatch-forward",
            pattern=re.compile(
                r'(\.\.\.[\w$]+\.strictMcpConfig\?\["--strict-mcp-config"\]:\[\])'
                r'((?:,\.\.\.[\w$]+\.restricted\?\["--restricted"\]:\[\])?)\]'
            ),
            replacement=_dispatch_forward_replacement,
        ),
        Patch(
            name="dev-channel-bg-register",
            pattern=re.compile(
                rf"if\(({_ID})&&\1\.length>0\)({_ID})=({_ID})\(\1,\"--channels\"\),"
                rf"({_ID})\(\2\);if\(!{_ID}\)\{{if\(({_ID})&&\5\.length>0\)"
                rf"{_ID}=\3\(\5,\"--dangerously-load-development-channels\"\)\}}"
            ),
            replacement=_dev_channel_replacement,
        ),
    ),
    verify_present=(
        re.compile(
            r'"--channels",(?:"--watch-artifact","--watch-artifact-no-autoreact",)?'
            r'"--dangerously-load-development-channels","--permission-prompt-tool"'
        ),
        re.compile(
            r'"--file","--channels","--dangerously-load-development-channels"\]'
        ),
        re.compile(r'strictMcpConfig\?\["--strict-mcp-config"\]:\[\],\.\.\.\(\(\)=>'),
        re.compile(r'CLAUDE_CODE_SESSION_KIND==="bg"&&[\w$]+&&[\w$]+\.length>0\)'),
    ),
    verify_absent=(re.compile(r"CLAUDE_DEV_CHANNELS"),),
    min_version=_V_2_1_151,
)

# --- provider environment inheritance for background agents (2.1.174) --------
#
# A background daemon can outlive the client that dispatches a job. Send the
# current provider configuration for each authenticated socket dispatch. Do not
# store this configuration in job files. A null value removes a value that a
# cold or prewarmed worker inherited from an earlier process.
_PROVIDER_ENV_PROTOCOL_VERSION = 3
_PROVIDER_ENV_GROUPS = re.compile(
    rf'(?P<selection>{_ID})=\["CLAUDE_CODE_USE_BEDROCK",'
    rf'(?P<selection_tail>.{{0,1000}}?)\],(?P<base_urls>{_ID})='
    r'\["ANTHROPIC_BASE_URL",'
    rf'(?P<base_url_tail>.{{0,1000}}?)\],(?P<credentials>{_ID})='
    r'\["ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN",'
    rf'(?P<credential_tail>.{{0,1000}}?)\],(?P<skip_auth>{_ID})='
    r'\["CLAUDE_CODE_SKIP_BEDROCK_AUTH",'
    rf'(?P<skip_auth_tail>.{{0,1000}}?)\],(?P<models>{_ID})='
    r'\["ANTHROPIC_MODEL",'
    rf'(?P<model_tail>.{{0,3000}}?)\],(?P<custom_models>{_ID})='
    r'\["ANTHROPIC_CUSTOM_MODEL_OPTION",'
    rf'(?P<custom_model_tail>.{{0,1000}}?)\],'
    rf'(?:(?P<cloud_credentials>{_ID})=\["AWS_ACCESS_KEY_ID",'
    rf'"AWS_SECRET_ACCESS_KEY","AWS_SESSION_TOKEN"\],'
    rf'(?P<cloud_config>{_ID})=\[\.\.\.(?P=cloud_credentials),'
    rf'"AWS_PROFILE","AWS_CONFIG_FILE","AWS_SHARED_CREDENTIALS_FILE",'
    rf'"GOOGLE_APPLICATION_CREDENTIALS","GOOGLE_CLOUD_PROJECT"\];)?'
    rf'(?P<recognized>{_ID})=new Set'
)
_PROVIDER_ENV_SNAPSHOT = re.compile(
    rf'function (?P<snapshot>{_ID})\(\)\{{let (?P<result>{_ID})=\{{\}};'
    rf'for\(let (?P<key>{_ID}) of (?P<allowlist>{_ID})\)\{{let '
    rf'(?P<value>{_ID})=process\.env\[(?P=key)\];if\((?P=value)===void 0\)continue;'
    rf'(?P<normalization>if\((?P<boolean_keys>{_ID})\.has\((?P=key)\)\)\{{'
    rf'if\((?P<truthy>{_ID})\((?P=value)\)\)(?P=result)\[(?P=key)\]="1";continue\}})?'
    rf'if\((?P=value)===""&&(?P=key)!=="CLAUDE_SECURESTORAGE_CONFIG_DIR"\)continue;'
    rf'(?P=result)\[(?P=key)\]=(?P=value)\}}return (?P=result)\}}'
)
_PROVIDER_ENV_SCHEMA = re.compile(
    rf'(?<![\w$])(?:(?P<schema>{_ID})\.object|(?P<object>{_ID}))\(\{{proto:(?P<proto>{_ID}),op:'
    rf'(?(schema)(?P=schema)\.literal|{_ID})\("dispatch"\),d:(?P<dispatch>{_ID})\(\),'
    rf'timeoutMs:(?(schema)(?P=schema)\.number|(?P<number>{_ID}))\(\),auth:'
)
_PROVIDER_ENV_PERSISTED_DEFAULT = re.compile(
    rf'(?<![\w$])(?P<isolation>{_ID})=(?P<default>(?:{_ID}\?\.bgIsolation==="default"\?void 0:)?)'
    rf'(?P<source>{_ID})==="repl"\?"none":'
    rf'(?P<options>{_ID})\?\.bgIsolation,(?P<provider>{_ID})='
    rf'(?P=options)\?\.providerEnv\?\?(?P<snapshot>{_ID})\(\),'
)
_PROVIDER_ENV_SOCKET = re.compile(
    rf'(?<![\w$])(?P<call>{_ID})\(\{{proto:(?P<proto>{_ID}),op:"dispatch",d:'
    rf'\{{\.\.\.(?P<job>{_ID}),nonce:(?P<nonce>[^}}]+)\}},timeoutMs:5000,'
    rf'auth:await (?P<auth>{_ID})\(\)\}}'
)
_PROVIDER_ENV_AGENTS_FALLBACK = re.compile(
    rf'if\((?P<gate>{_ID})\("tengu_bg_leftarrow_inprocess",!0\)\)'
    rf'try\{{return await (?P<inprocess>{_ID})\((?P<job>{_ID}),'
    rf'(?P<context>{_ID}),\{{(?:\.\.\.{_ID}\(\)&&\{{dispatchExtraArgs:\["--restricted"\]\}},)?dispatchDefaults:(?P<defaults>{_ID})'
    rf'(?:,\.\.\.(?P<selection>{_ID})\?\.autoOpenJobId!==void 0&&'
    rf'\{{autoOpenJobId:(?P=selection)\.autoOpenJobId\}})?'
    rf'(?:,originSpawn:{_ID})?(?:,storageV5:{_ID})?(?:,credentials:{_ID})?'
    rf'(?:,fleetNudgeStore:(?P=selection)\?\.fleetNudgeStore)?'
    rf'(?:,dispatchExtraArgs:[^{{}}]{{1,500}})?\}}\)\}}'
    rf'catch\((?P<error>{_ID})\)\{{(?P<log>{_ID})\((?P=error)\)\}}'
    rf'(?:return |let {_ID}=await )(?P<spawn>{_ID})\(\{{args:\["agents",'
    rf'\.\.\.(?P<serialize>{_ID})\((?P=defaults)\)'
    rf'(?:,\.\.\.(?:_ccAgentsDispatchArgs|globalThis\.__ccpatchRuntime\.agentsHandoff\.dispatchArgs)\(\))?\],'
    rf'env:\{{CLAUDE_AGENTS_SELECT:'
    rf'(?(selection)(?P=selection)\?\.autoOpenJobId\?\?)(?P=job),'
    rf'\.\.\.(?P<accessibility>{_ID})\(\)(?:,\.\.\.{_ID}\(\)&&\{{CLAUDE_CODE_RESTRICTED:"1"\}})?\}}\}}\)'
)
_PROVIDER_ENV_SOCKET_RESULT = re.compile(
    rf'if\((?P<response>{_ID})\.ok&&(?P=response)\.op==="dispatch"\)'
    rf'(?P<success>return .{{0,500}}?);if\("code"in (?P=response)&&'
)
_PROVIDER_ENV_REDISPATCH_RESULT = re.compile(
    rf'if\((?P<response>{_ID})\.ok&&(?P=response)\.op==="dispatch"\)'
    rf'(?P<success>return .{{0,1000}}?\{{ok:!0,short:(?P<short>{_ID}),'
    rf'sessionId:(?P<session>{_ID}),idle:(?P<idle>{_ID}),name:(?P<name>{_ID}),'
    rf'rescued:!0\}})'
)
_PROVIDER_ENV_DAEMON_ACK = re.compile(
    rf'return (?P<respond>{_ID})\((?P<socket>{_ID}),\{{ok:!0,op:(?P<op>{_ID}),'
    rf'short:(?P<short>{_ID}),pid:(?P<worker>{_ID})\.record\.pid,'
)
_PROVIDER_ENV_CONTROL = re.compile(
    rf'case"dispatch":if\(!(?P<auth_check>{_ID})\((?P<request>{_ID})\.auth,'
    rf'(?P<control_key>{_ID})\)\)return (?P<respond>{_ID})\((?P<socket>{_ID}),'
    rf'(?P<auth_error>\{{ok:!1,.{{0,300}}?code:"EAUTH"\}})\);if\(await '
    rf'(?P<yield>{_ID})\(0\),(?P=socket)\.readableEnded\|\|(?P=socket)\.destroyed\)'
    rf'(?P<stale>.{{0,300}}?)return (?P<wait>{_ID})\((?P<handles>{_ID}),(?:{_ID},)?(?P=socket),'
    rf'"dispatch",(?P=request)\.d\.short,(?P=request)\.d\.nonce,'
    rf'(?P=request)\.timeoutMs,(?P<dispatch_cb>{_ID})\((?P=request)\.d\)'
)
_PROVIDER_ENV_WORKER = re.compile(
    rf'function (?P<env_builder>{_ID})\((?P<job>{_ID}),(?P<job_dir>{_ID}),'
    rf'(?P<snapshot_path>{_ID}),(?P<rv_sock>{_ID}),(?P<socket_auth>{_ID})\)'
    rf'\{{let (?P<ambient>{_ID})=\{{\.\.\.process\.env\}}(?:,|;if\((?P<normalize>{_ID})\((?P=ambient)\),(?P=job)\.env\)(?P=normalize)\((?P=job)\.env\);let )(?P<env>{_ID})='
    rf'\{{\.\.\.(?P=ambient),(?P<body>.{{0,2000}}?)\}}'
    rf'(?P<path_normalization>,(?P<path_key>{_ID})=Object\.hasOwn\((?P=ambient),"PATH"\)'
    rf'\?"PATH":Object\.keys\((?P=ambient)\)\.find\(\((?P<path_candidate>{_ID})\)=>'
    rf'(?P=path_candidate)\.toUpperCase\(\)==="PATH"\),'
    rf'(?P<path_value>{_ID})=(?P=job)\.env\?\.PATH\|\|'
    rf'\((?P=path_key)\?(?P=ambient)\[(?P=path_key)\]:void 0\);'
    rf'for\(let (?P<path_entry>{_ID}) of Object\.keys\((?P=env)\)\)'
    rf'if\((?P=path_entry)\.toUpperCase\(\)==="PATH"\)delete (?P=env)\[(?P=path_entry)\];'
    rf'if\((?P=path_value)\)(?P=env)\[(?P=path_key)\?\?"PATH"\]=(?P=path_value))?'
    rf';if\(process\.env\.'
)
_PROVIDER_ENV_WORKER_FINAL = re.compile(
    rf'(?P<prefix>function (?P<env_builder>{_ID})\((?P<job>{_ID}),(?P<job_dir>{_ID}),'
    rf'(?P<snapshot_path>{_ID}),(?P<rv_sock>{_ID}),(?P<socket_auth>{_ID}),'
    rf'_ccProviderEnv\)\{{.{{0,1500}}?let _ccProviderPayload=.{{0,5000}}?)'
    rf'return (?P<env>{_ID})\}}'
)
_PROVIDER_ENV_PATCHED_SNAPSHOT = re.compile(
    rf'function _ccProviderKeys\(\).{{0,7000}}?function (?P<snapshot>{_ID})\(\)'
    rf'\{{let {_ID}=\{{\}};for\(let {_ID} of _ccProviderKeys\(\)\)'
)
_PROVIDER_ENV_MANAGER = re.compile(
    rf'(?<![\w$])(?P<class_name>{_ID})\{{dispatch;spawnPty;getAuthSnapshot;via;(?:storageV5;(?:credentials;)?)?record;'
)
_PROVIDER_ENV_CONSTRUCTOR = re.compile(
    rf'constructor\((?P<job>{_ID}),(?P<spawn>{_ID}),(?P<auth>{_ID}),'
    rf'(?P<via>{_ID}),(?P<record>{_ID})(?P<native_tail>(?:,{_ID}(?:,{_ID})?)?)\)\{{this\.dispatch=(?P=job);'
)
_PROVIDER_ENV_STATIC_SPAWN = re.compile(
    rf'static spawn\((?P<job>{_ID}),(?P<spawn>{_ID}),(?P<auth>{_ID}),'
    rf'(?P<options>{_ID})(?P<tail>(?:,{_ID}(?:,{_ID})?)?)\)\{{let (?P<worker>{_ID})=new (?P<class_name>{_ID})'
    rf'\((?P=job),(?P=spawn)\?\?(?P<default_spawn>{_ID})\(\),(?P=auth),"cold"(?P<constructor_tail>(?:,void 0,{_ID}(?:,{_ID})?)?)\);'
)
_PROVIDER_ENV_STATIC_CLAIM = re.compile(
    rf'static claim\((?P<job>{_ID}),(?P<options>{_ID})\)\{{let '
    rf'(?P<worker>{_ID})=new (?P<class_name>{_ID})\((?P=job),'
    rf'(?P=options)\.spawnPty,(?P=options)\.getAuthSnapshot,"spare",'
    rf'(?P<record>\{{pid:.{{0,1000}}?\.VERSION\}})(?P<tail>(?:,(?P=options)\.storageV5(?:,(?P=options)\.credentials)?)?)\);'
)
_PROVIDER_ENV_CLAIM_FRAME = re.compile(
    rf'static buildClaimFrame\((?P<job>{_ID}),(?P<snapshot>{_ID}),'
    rf'(?P<auth>{_ID})\)\{{let (?P<job_dir>{_ID})=(?P<job_dir_fn>{_ID})'
    rf'\((?P=job)\.short\),(?P<env>{_ID})=(?P<env_builder>{_ID})\('
    rf'(?P=job),(?P=job_dir),(?P=snapshot),(?P<rv_sock>{_ID})'
    rf'\((?P=job)\.short\),(?P=auth)\);'
)
_PROVIDER_ENV_DO_SPAWN = re.compile(
    rf'let (?P<argv>{_ID})=(?P<argv_fn>{_ID})\((?P<job>{_ID}),this\.attempt,'
    rf'(?P<has_messages>{_ID}),(?P<session>{_ID}),'
    rf'(?:(?P<transcript_path>{_ID}),)?(?P<flags>{_ID})\)[,;]'
    rf'(?:this\.bootedViaResume=.{{0,600}}?;let )?(?P<env>{_ID})=(?P<env_builder>{_ID})\((?P=job),(?P<job_dir>{_ID}),'
    rf'(?P<snapshot>{_ID}),this\.rvSockPath\?\?(?P<rv_sock>{_ID})'
    rf'\((?P=job)\.short\),this\.socketAuth\(\)\);'
)
_PROVIDER_ENV_CLAIM_CALL = re.compile(
    rf'(?P<claim>{_ID})\((?P<job>{_ID}),(?P<spare>{_ID}),(?P<spawn>{_ID}),'
    rf'(?P<auth>{_ID})\)\{{(?:(?P=spare)\.claimed=!0;)?'
    rf'let (?P<worker>{_ID})=(?P<class_name>{_ID})\.claim'
    rf'\((?P=job),(?P<options>\{{pid:(?P=spare)\.hostPid,.{{0,300}}?\}})\);'
)
_PROVIDER_ENV_BUILD_CLAIM_CALL = re.compile(
    rf'function (?P<frame>{_ID})\((?P<job>{_ID}),(?P<snapshot>{_ID}),'
    rf'(?P<auth>{_ID}),(?P<claim_auth>{_ID})\)\{{let\{{env:(?P<env>{_ID}),'
    rf'argv:(?P<argv>{_ID})\}}=(?P<class_name>{_ID})\.buildClaimFrame\('
    rf'(?P=job),(?P=snapshot),(?P=auth)\);'
)
_PROVIDER_ENV_CLAIMED_SPARE_FRAME = re.compile(
    rf'function (?P<claim>{_ID})\((?P<job>{_ID}),(?P<spare>{_ID}),'
    rf'(?P<spawn>{_ID}),(?P<auth>{_ID})(?P<tail>(?:,{_ID}(?:,{_ID})?)?)\)\{{(?:(?P=spare)\.claimed=!0;)?'
    rf'let (?P<worker>{_ID})='
    rf'(?P<class_name>{_ID})\.claim\((?P=job),(?P<options>\{{.{{0,500}}?\}})\);'
    rf'return (?P<snapshot>{_ID})\((?P=job)\.short,(?P=auth)\?\.\(\)\)\.then\('
    rf'\((?P<snapshot_arg>{_ID})\)=>(?P<send>{_ID})\((?P=spare)\.claimSock,'
    rf'(?P<frame>{_ID})\((?P=job),(?P=snapshot_arg),(?P=worker)\.socketAuth\(\),'
    rf'(?P=spare)\.claimAuth\)\)\)'
)
_PROVIDER_ENV_MANAGER_DISPATCH = re.compile(
    rf'(?<![\w$])(?P<dispatch>{_ID})=async\((?P<job>{_ID}),(?P<retry>{_ID})=0,'
    rf'(?P<after_upgrade>{_ID})(?P<native_tail>,{_ID}=!1)?\)=>\{{'
)
_PROVIDER_ENV_MANAGER_RETRY = re.compile(
    rf'return await (?P<delay>{_ID})\(100\),(?P<dispatch>{_ID})\('
    rf'(?P<job>{_ID}),(?P<retry>{_ID})\+1,(?P<after_upgrade>{_ID})(?:,{_ID})?\)'
)
_PROVIDER_ENV_MANAGER_CLAIM = re.compile(
    rf'let (?P<worker>{_ID})=(?P<claim>{_ID})\((?P<job>{_ID}),'
    rf'(?P<spare>{_ID}),(?P<spawn>{_ID}),(?P<auth_obj>{_ID})\.getAuthSnapshot(?:,(?P=auth_obj)\.storageV5(?:,(?P=auth_obj)\.credentials)?)?\)'
)
_PROVIDER_ENV_MANAGER_SPAWN = re.compile(
    rf'(?<![\w$])(?P<class_name>{_ID})\.spawn\((?P<job>{_ID}),(?P<spawn>{_ID}),'
    rf'(?P<auth_obj>{_ID})\.getAuthSnapshot,(?P<after_upgrade>{_ID})\?'
    rf'\{{afterUpgrade:(?P=after_upgrade)\}}:void 0(?:,(?P=auth_obj)\.storageV5(?:,(?P=auth_obj)\.credentials)?)?\)'
)
_PROVIDER_ENV_CLAIMED_ENTRY = re.compile(
    rf'(?P<prefix>async function (?P<entry>{_ID})\((?P<claim>{_ID}),(?P<main>{_ID})\)'
    rf'\{{.{{0,1000}}?Object\.assign\(process\.env,(?P=claim)\.env\),'
    rf'process\.argv=.{{0,300}}?),(?P<initializers>(?P<cache>{_ID})\(\),'
    rf'(?P<auth>{_ID})\(\),.{{0,300}}?);let\{{main:(?P<worker_main>{_ID})\}}='
    rf'await (?P=main);await (?P=worker_main)\(\)\}}'
)
_PROVIDER_ENV_PREACTION_START = re.compile(
    rf'(?P<hook>\.hook\("preAction",async\((?P<command>{_ID}),(?P<action>{_ID})\)'
    rf'=>\{{(?P<marker>{_ID})\("preAction_start"\);)'
)
_PROVIDER_ENV_PREACTION_INITIALIZED = re.compile(
    rf'(?:await (?P<initializer>{_ID})\(\),)?(?P<marker>{_ID})\("preAction_after_init"\)'
)
_PROVIDER_ENV_OPERATIONAL_ENTRY = re.compile(
    rf'(?P<guard>if\((?P<noninteractive>{_ID})\)\{{[\s\S]{{0,2500}}?)(?P<settings>{_ID})\(\),'
    rf'(?P<telemetry>{_ID})\(\);'
    rf'(?P<warning>let (?P<warning_var>{_ID})=\({_ID}\.continue\|\|{_ID}\.resume\|\|{_ID}\)'
    rf'&&!{_ID}\(\)\?null:{_ID}\({_ID}\?\?{_ID}\);'
    rf'if\((?P=warning_var)&&{_ID}!=="json"&&{_ID}!=="stream-json"\)'
    rf'{_ID}\((?P=warning_var)\);)?let (?P<start>{_ID})=performance\.now\(\),'
)
_PROVIDER_ENV_DELAYED_SETTINGS = re.compile(
    rf'function (?P<telemetry>{_ID})\(\)\{{(?P<prefix>.{{0,1000}}?Waiting for remote '
    rf'managed settings before telemetry init"\),)(?P<wait>{_ID})\(\)\.then\(async\(\)=>'
    rf'\{{(?P<loaded>.{{0,300}}?Remote managed settings loaded, initializing telemetry"\),)'
    rf'(?P<before_initialize>(?P<settings>{_ID})\(\)(?:,|;'
    rf'let\[(?P<ca_changed>{_ID}),(?P<mtls_changed>{_ID})\]='
    rf'await Promise\.all\(\[{_ID}\(\),{_ID}\(\)\]\);'
    rf'if\((?P=ca_changed)\|\|(?P=mtls_changed)\){_ID}\(\),{_ID}\(\);))'
    rf'await (?P<initialize>{_ID})\(\)'
)
# Keep the known normalization layouts until discovery includes the serialization
# sink. A rest binding alone does not identify the persisted state object.
_PROVIDER_ENV_STATE_WRITE = re.compile(
    rf'async function (?P<write>{_ID})\((?P<dir>{_ID}),(?P<state>{_ID})(?:,{_ID})?\)\{{let'
    rf'(?: (?P<cron>{_ID})=(?P=state)\.inFlight\?\.kinds\.includes\("session_cron"\)===!0,'
    rf'(?P<normalized>{_ID})=(?P=cron)&&!(?P=state)\.selfWake(?:&&{_ID}\((?P=state)\.inFlight\))?\?'
    rf'\{{\.\.\.(?P=state),selfWake:!0\}}:!(?P=cron)&&(?P=state)\.selfWake&&'
    rf'{_ID}\((?P=state)\)\?\{{\.\.\.(?P=state),selfWake:void 0\}}:(?P=state),)?'
    rf'(?:(?P<terminal>{_ID})={_ID}\((?P=normalized)\)\?(?P=normalized)\.lastTerminalAt\?(?P=normalized):\{{\.\.\.(?P=normalized),lastTerminalAt:(?P=normalized)\.updatedAt\}}:(?P=normalized)\.lastTerminalAt!==void 0\?\{{\.\.\.(?P=normalized),lastTerminalAt:void 0\}}:(?P=normalized),)?'
    rf'\{{pinned:(?P<pinned>{_ID}),sortOrder:(?P<sort>{_ID}),stateSortOrder:'
    rf'(?P<state_sort>{_ID}),(?(normalized)group:{_ID},)'
    rf'\.\.\.(?P<rest>{_ID})\}}=(?(terminal)(?P=terminal)|(?(normalized)(?P=normalized)|(?P=state)))(?P<separator>[;,])'
)
_PROVIDER_ENV_STATE_VIEW = re.compile(
    rf'bgIsolation:(?P<job>{_ID})\.bgIsolation,providerEnv:(?P=job)\.providerEnv,'
)
_PROVIDER_ENV_STATE_SCHEMA = re.compile(
    rf'providerEnv:(?:(?P<schema>{_ID})\.record\((?P=schema)\.string\(\),'
    rf'(?P=schema)\.string\(\)\)|{_ID}\({_ID}\(\),{_ID}\(\)\))\.transform\((?:{_ID}|'
    rf'\((?P<value>{_ID})\)=>\{{let (?P<filtered>{_ID})={_ID}\((?P=value)\);'
    rf'return (?P=filtered)&&{_ID}\((?P=filtered),{_ID}\)\}})\)\.optional\(\),'
)
_PROVIDER_ENV_JOB_COPY = re.compile(rf'providerEnv:(?P<prior>{_ID})\?\.providerEnv,')
_PROVIDER_ENV_SEED_STATE = re.compile(
    rf'providerEnv:(?P<snapshot>{_ID})\(\),sessionPermissionRules:'
)
_PROVIDER_ENV_RESPAWN_GUARD = re.compile(r'\|\|(?P<job>[\w$]+)\.providerEnv(?![\w$])')
_PROVIDER_ENV_RESPAWN_OPTION = re.compile(
    rf',\.\.\.(?P<job>{_ID})\.providerEnv&&\{{providerEnv:(?P=job)\.providerEnv\}}'
)


def _provider_groups(source: str) -> dict[str, str]:
    # Match each group separately so intervening native policy tables remain intact.
    prefixes = {
        "selection": '"CLAUDE_CODE_USE_BEDROCK","CLAUDE_CODE_USE_VERTEX"',
        "base_urls": '"ANTHROPIC_BASE_URL",',
        "credentials": '"ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN"',
        "skip_auth": '"CLAUDE_CODE_SKIP_BEDROCK_AUTH","CLAUDE_CODE_SKIP_VERTEX_AUTH"',
        "models": '"ANTHROPIC_MODEL",',
        "custom_models": '"ANTHROPIC_CUSTOM_MODEL_OPTION",',
        "cloud_credentials": '"AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY","AWS_SESSION_TOKEN"',
    }
    if (
        '"_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL","ANTHROPIC_BEDROCK_BASE_URL"'
        in source
    ):
        prefixes["base_urls"] = (
            '"ANTHROPIC_BASE_URL","_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL","ANTHROPIC_BEDROCK_BASE_URL"'
        )
    if '"ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","CLAUDE_CODE_OAUTH_TOKEN"' in source:
        prefixes["credentials"] = (
            '"ANTHROPIC_API_KEY","ANTHROPIC_AUTH_TOKEN","CLAUDE_CODE_OAUTH_TOKEN"'
        )
    groups: dict[str, str] = {}
    cloud_name: str | None = None
    for name, prefix in prefixes.items():
        suffix = (
            r'(?=[^\]]*"ANTHROPIC_FOUNDRY_RESOURCE")'
            if name == "selection" and '"ANTHROPIC_FOUNDRY_RESOURCE"' in source
            else ""
        )
        pattern = rf'(?<![\w$.])(?P<{name}>{_ID})=\[{re.escape(prefix)}{suffix}'
        if name == "cloud_credentials":
            pattern += rf'\],{_ID}=\[\.\.\.(?P=cloud_credentials),"AWS_PROFILE",'
        candidates: dict[int, re.Match[str]] = {}
        for anchor in re.finditer(re.escape("=[" + prefix), source):
            match = re.compile(pattern).search(
                source, max(0, anchor.start() - 100), anchor.start() + 2000
            )
            if match is not None:
                candidates[match.start()] = match
        matches = list(candidates.values())
        if not matches and name == "cloud_credentials":
            continue
        if len(matches) != 1:
            raise PatchError(
                f"background-provider-environment: provider group {name} absent or ambiguous"
            )
        groups[name] = matches[0].group(name)
        if name == "cloud_credentials":
            cloud_name = matches[0].group(name)
    if cloud_name is not None:
        pattern = rf'(?<![\w$.])(?P<cloud_config>{_ID})=\[\.\.\.{re.escape(cloud_name)},"AWS_PROFILE",'
        matches = [
            match
            for anchor in re.finditer(
                re.escape('=[...' + cloud_name + ',"AWS_PROFILE",'), source
            )
            if (
                match := re.compile(pattern).search(
                    source, max(0, anchor.start() - 100), anchor.end()
                )
            )
            is not None
        ]
        if len(matches) != 1:
            raise PatchError(
                "background-provider-environment: cloud configuration absent or ambiguous"
            )
        groups["cloud_config"] = matches[0].group("cloud_config")
    return groups


_PROVIDER_ENV_VERTEX_REGION_KEYS = (
    "VERTEX_REGION_CLAUDE_FABLE_5",
    "VERTEX_REGION_CLAUDE_HAIKU_4_5",
    "VERTEX_REGION_CLAUDE_3_5_HAIKU",
    "VERTEX_REGION_CLAUDE_3_5_SONNET",
    "VERTEX_REGION_CLAUDE_3_7_SONNET",
    "VERTEX_REGION_CLAUDE_4_8_OPUS",
    "VERTEX_REGION_CLAUDE_4_7_OPUS",
    "VERTEX_REGION_CLAUDE_4_6_OPUS",
    "VERTEX_REGION_CLAUDE_4_5_OPUS",
    "VERTEX_REGION_CLAUDE_4_1_OPUS",
    "VERTEX_REGION_CLAUDE_4_0_OPUS",
    "VERTEX_REGION_CLAUDE_5_SONNET",
    "VERTEX_REGION_CLAUDE_4_6_SONNET",
    "VERTEX_REGION_CLAUDE_4_5_SONNET",
    "VERTEX_REGION_CLAUDE_4_0_SONNET",
)
_PROVIDER_ENV_EXPLICIT_KEYS = (
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_INTERNAL_FC_OVERRIDES",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "CLAUDE_SECURESTORAGE_CONFIG_DIR",
    "ANTHROPIC_UNIX_SOCKET",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CLAUDE_CODE_HOST_AUTH_ENV_VAR",
    "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
    "CLAUDE_CODE_HOST_AUTH_REFRESH_TIMEOUT_MS",
    "ANTHROPIC_BEDROCK_SERVICE_TIER",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_CERT_STORE",
    "CC_KIMI_AUTH_TOKEN",
    "CC_ZAI_AUTH_TOKEN",
    "CC_MINIMAX_AUTH_TOKEN",
    "CC_OPENAI_PROXY_AUTH_TOKEN",
    "CC_OPENAI_PROXY_EFFECTIVE_URL",
    "CC_OPENAI_AVAILABLE",
    *_PROVIDER_ENV_VERTEX_REGION_KEYS,
)


def _provider_key_sources(source: str) -> tuple[str, ...]:
    groups = _provider_groups(source)
    snapshots = list(_PROVIDER_ENV_SNAPSHOT.finditer(source))
    if len(snapshots) != 1:
        raise PatchError(
            "background-provider-environment: provider snapshot absent or ambiguous"
        )
    return (
        snapshots[0].group("allowlist"),
        groups["selection"],
        groups["base_urls"],
        groups["credentials"],
        groups["skip_auth"],
        groups["models"],
        groups["custom_models"],
        *(
            (groups["cloud_credentials"], groups["cloud_config"])
            if "cloud_credentials" in groups
            else ()
        ),
    )


def _provider_keys_expression(source: str) -> str:
    sources = _provider_key_sources(source)
    explicit = tuple(f'"{key}"' for key in _PROVIDER_ENV_EXPLICIT_KEYS)
    return "[" + ",".join((*[f"...{name}" for name in sources], *explicit)) + "]"


def _replace_provider_snapshot(match: re.Match[str]) -> str:
    snapshot = match.group("snapshot")
    result = match.group("result")
    key = match.group("key")
    value = match.group("value")
    sources = _provider_key_sources(match.string)
    keys = _provider_keys_expression(match.string)
    unavailable = "||".join(f"{source}==null" for source in sources)
    key_error = (
        'Object.assign(Error("Background provider environment key registry is '
        'unavailable"),{code:"EPROVIDERENV"})'
    )
    payload_error = (
        'Object.assign(Error("Background provider environment transient payload is '
        'unavailable; dispatch again from the current Claude Code session"),'
        '{code:"EPROVIDERENV"})'
    )
    invalid_error = (
        'Object.assign(Error("Background provider environment transient payload is '
        'invalid; dispatch again from the current Claude Code session"),'
        '{code:"EPROVIDERENV"})'
    )
    return (
        f"function _ccProviderKeys(){{if({unavailable})throw {key_error};"
        f"return {keys}}}function _ccRequireProviderEnv(_ccProviderEnv){{"
        'if(_ccProviderEnv==null||typeof _ccProviderEnv!=="object"||'
        f"Array.isArray(_ccProviderEnv))throw {payload_error};"
        "let _ccAllowed=new Set(_ccProviderKeys());"
        "for(let[_ccKey,_ccValue]of Object.entries(_ccProviderEnv))"
        "if(!_ccAllowed.has(_ccKey)||(_ccValue!==null&&"
        f'typeof _ccValue!=="string"))throw {invalid_error};'
        "return _ccProviderEnv}"
        'function _ccProviderMac(_ccToken,_ccReply){return require("crypto")'
        '.createHmac("sha256",_ccToken).update(JSON.stringify(['
        '"cc-provider-snapshot",_ccReply.proto,_ccReply.version,_ccReply.sessionId,'
        '_ccReply.nonce,_ccReply.payload])).digest("hex")}'
        "function _ccProviderRetain(_ccProviderEnv){"
        "return Object.freeze({..._ccRequireProviderEnv(_ccProviderEnv)})}"
        "function _ccProviderCaptureTransport(_ccEnv=process.env){let _ccSerialized="
        "_ccEnv.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT;delete "
        "_ccEnv.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT;if(_ccSerialized===void 0)"
        "return null;let _ccProviderEnv;try{_ccProviderEnv=JSON.parse(_ccSerialized)}"
        'catch{throw Object.assign(Error("Background provider environment transient '
        'transport is invalid; dispatch again from the current Claude Code session"),'
        '{code:"EPROVIDERENV"})}if(_ccProviderEnv===null||typeof _ccProviderEnv!=="object"||'
        f'Array.isArray(_ccProviderEnv))throw {invalid_error};'
        'for(let _ccValue of Object.values(_ccProviderEnv))'
        f'if(_ccValue!==null&&typeof _ccValue!=="string")throw {invalid_error};'
        'return Object.freeze(_ccProviderEnv)}'
        "let _ccProviderWorkerEnv=_ccProviderCaptureTransport();"
        "function _ccProviderApplyWorkerFinal(){if(_ccProviderWorkerEnv!==null)"
        "return _ccProviderApplyFinal(_ccProviderWorkerEnv);if(process.env."
        'CLAUDE_CODE_SESSION_KIND==="bg")throw Object.assign(Error("Background provider '
        "environment transient payload is unavailable; dispatch again from the current "
        'Claude Code session"),{code:"EPROVIDERENV"})}'
        "function _ccProviderApplyFinal(_ccProviderEnv){"
        "_ccProviderEnv=_ccRequireProviderEnv(_ccProviderEnv);"
        "for(let _ccKey of _ccProviderKeys())delete process.env[_ccKey];"
        "for(let[_ccKey,_ccValue]of Object.entries(_ccProviderEnv))"
        "if(_ccValue!==null)process.env[_ccKey]=_ccValue}"
        f"function {snapshot}(){{let {result}={{}};"
        f"for(let {key} of _ccProviderKeys()){{let {value}=process.env[{key}];"
        + (
            f'if({match.group("boolean_keys")}.has({key})){{'
            f'{result}[{key}]={match.group("truthy")}({value})?"1":null;continue}}'
            if match.group("normalization")
            else ""
        )
        + f"{result}[{key}]={value}===void 0?null:{value}}}return {result}}}"
    )


def _replace_provider_schema(match: re.Match[str]) -> str:
    schema = match.group("schema")
    if schema is None:
        number = match.group("number")
        object_constructor = match.group("object")
        return checked_replace(
            match.group(0),
            "timeoutMs:",
            f"providerEnvVersion:{number}().optional(),"
            f"providerEnv:{object_constructor}({{}}).passthrough().transform(_ccProviderRetain).optional(),timeoutMs:",
            context="provider named schema fields",
        )
    replacement = (
        f"providerEnvVersion:{schema}.number().optional(),"
        f"providerEnv:{schema}.record({schema}.enum(_ccProviderKeys()),"
        f"{schema}.union([{schema}.string(),{schema}.null()])).optional(),"
        "timeoutMs:"
    )
    return checked_replace(
        match.group(0), "timeoutMs:", replacement, context="provider schema timeout"
    )


def _provider_snapshot_reference(match: re.Match[str], name: str) -> str:
    definition = _PROVIDER_ENV_PATCHED_SNAPSHOT.search(match.string)
    assert definition is not None
    return module_reference(match.string, definition.start(), name, match.start())


def _replace_provider_socket(match: re.Match[str]) -> str:
    snapshot = discover_identifiers(match.string, (_PROVIDER_ENV_PATCHED_SNAPSHOT,))
    snapshot["snapshot"] = _provider_snapshot_reference(match, snapshot["snapshot"])
    version = _PROVIDER_ENV_PROTOCOL_VERSION
    return (
        f'{match.group("call")}({{proto:{match.group("proto")},op:"dispatch",'
        f'd:{{...{match.group("job")},nonce:{match.group("nonce")}}},'
        f'providerEnvVersion:{version},providerEnv:{snapshot["snapshot"]}(),'
        f'timeoutMs:5000,auth:await {match.group("auth")}()}}'
    )


def _replace_provider_agents_fallback(match: re.Match[str]) -> str:
    snapshot = discover_identifiers(match.string, (_PROVIDER_ENV_PATCHED_SNAPSHOT,))
    snapshot["snapshot"] = _provider_snapshot_reference(match, snapshot["snapshot"])
    return checked_replace(
        match.group(0),
        f'...{match.group("accessibility")}()',
        f'...{match.group("accessibility")}(),'
        f'CLAUDE_CODE_PROVIDER_ENV_TRANSIENT:JSON.stringify({snapshot["snapshot"]}())',
        context="provider agents fallback transport",
    )


def _provider_protocol_error() -> str:
    return (
        '{ok:!1,error:"Background provider environment protocol mismatch. Restart '
        'the stale Claude Code daemon and try again",code:"EPROVIDERENV"}'
    )


def _validated_provider_dispatch_response(response: str, success: str) -> str:
    version = _PROVIDER_ENV_PROTOCOL_VERSION
    return (
        f'if({response}.ok&&{response}.op==="dispatch"){{'
        f'if({response}.providerEnvVersion!=={version})'
        f"{response}={_provider_protocol_error()};else {success}}}"
    )


def _replace_provider_socket_result(match: re.Match[str]) -> str:
    response = match.group("response")
    stale_error = (
        f'if("code"in {response}&&{response}.code==="EPROVIDERENV")'
        f'return{{ok:!1,reason:"daemon-unreachable",detail:{response}.error}};'
    )
    validated_success = _validated_provider_dispatch_response(
        response, match.group("success")
    )
    return validated_success + ";" + stale_error + match.group(0).split(";", 1)[1]


def _replace_provider_redispatch_result(match: re.Match[str]) -> str:
    response = match.group("response")
    validated_success = _validated_provider_dispatch_response(
        response, match.group("success")
    )
    protocol_error = (
        f'if("code"in {response}&&{response}.code==="EPROVIDERENV")'
        f'throw Object.assign(Error({response}.error),{{code:"EPROVIDERENV"}});'
    )
    return validated_success + ";" + protocol_error


def _replace_provider_daemon_ack(match: re.Match[str]) -> str:
    return checked_replace(
        match.group(0),
        f'op:{match.group("op")},',
        f'op:{match.group("op")},providerEnvVersion:{_PROVIDER_ENV_PROTOCOL_VERSION},',
        context="provider daemon acknowledgement",
    )


def _provider_dispatch_padding(source: str) -> str:
    """Keep the native cwd-probe argument before the provider snapshot."""
    return (
        ",!1"
        if re.search(
            r"=async\([\w$]+,[\w$]+=0,[\w$]+,[\w$]+=!1(?:,_ccProviderEnv)?\)=>", source
        )
        else ""
    )


def _replace_provider_control(match: re.Match[str]) -> str:
    request = match.group("request")
    respond = match.group("respond")
    socket = match.group("socket")
    version = _PROVIDER_ENV_PROTOCOL_VERSION
    capability_check = (
        f'if({request}.providerEnvVersion!=={version}||{request}.providerEnv===void 0)'
        f'return {respond}({socket},{{ok:!1,error:"Background provider environment '
        'protocol mismatch. Restart the stale Claude Code daemon and try again",'
        'code:"EPROVIDERENV"});'
    )
    original = match.group(0)
    original = checked_replace(
        original,
        f"if(await {match.group('yield')}(0)",
        f"{capability_check}if(await {match.group('yield')}(0)",
        context="provider control capability check",
    )
    return checked_replace(
        original,
        f'{match.group("dispatch_cb")}({request}.d)',
        f'{match.group("dispatch_cb")}({request}.d,0,void 0{_provider_dispatch_padding(match.string)},{request}.providerEnv)',
        context="provider control dispatch callback",
    )


def _replace_provider_worker(match: re.Match[str]) -> str:
    env = match.group("env")
    prefix = checked_replace(
        match.group(0)[: -len("if(process.env.")],
        f'{match.group("socket_auth")}){{',
        f'{match.group("socket_auth")},_ccProviderEnv){{',
        context="provider worker signature",
    )
    return (
        f"{prefix}let _ccProviderPayload="
        "_ccProviderRetain(_ccProviderEnv);"
        f'{env}.CLAUDE_CODE_PROVIDER_ENV_TRANSIENT='
        "JSON.stringify(_ccProviderPayload);"
        f"for(let _ccKey of _ccProviderKeys())delete {env}[_ccKey];"
        f"for(let[_ccKey,_ccValue]of Object.entries(_ccProviderPayload))"
        f"if(_ccValue!==null){env}[_ccKey]=_ccValue;if(process.env."
    )


def _replace_provider_worker_final(match: re.Match[str]) -> str:
    env = match.group("env")
    return (
        match.group("prefix")
        + f'if({match.group("job")}.launch.mode!=="exec"){{'
        + f"for(let _ccKey of _ccProviderKeys())delete {env}[_ccKey];"
        + "for(let[_ccKey,_ccValue]of Object.entries(_ccProviderPayload))"
        + f"if(_ccValue!==null){env}[_ccKey]=_ccValue;}}return {env}}}"
    )


def _provider_final_apply(payload: str) -> str:
    if payload == "_ccProviderWorkerEnv":
        return "_ccProviderApplyWorkerFinal();"
    return f"_ccProviderApplyFinal({payload});"


def _replace_provider_claimed_entry(match: re.Match[str]) -> str:
    declaration = (
        f"async function {match.group('entry')}({match.group('claim')},"
        f"{match.group('main')}){{"
    )
    prefix = checked_replace(
        match.group("prefix"),
        declaration,
        declaration
        + f"_ccProviderWorkerEnv=_ccProviderCaptureTransport({match.group('claim')}.env);",
        context="provider claimed-entry declaration",
    )
    return (
        prefix
        + ","
        + match.group("initializers")
        + f";let{{main:{match.group('worker_main')}}}=await {match.group('main')};"
        + _provider_final_apply("_ccProviderWorkerEnv")
        + f"await {match.group('worker_main')}()}}"
    )


def _replace_provider_preact_start(match: re.Match[str]) -> str:
    return match.group("hook")


def _replace_provider_preact_initialized(match: re.Match[str]) -> str:
    return match.group(0) + ",_ccProviderApplyWorkerFinal()"


def _replace_provider_operational_entry(match: re.Match[str]) -> str:
    return (
        match.group("guard")
        + f"{match.group('settings')}(),{match.group('telemetry')}(_ccProviderWorkerEnv);"
        + "_ccProviderApplyWorkerFinal();"
        + (match.group("warning") or "")
        + f"let {match.group('start')}=performance.now(),"
    )


def _replace_provider_delayed_settings(match: re.Match[str]) -> str:
    return (
        f"function {match.group('telemetry')}(_ccProviderWorkerEnv){{{match.group('prefix')}"
        f"{match.group('wait')}().then(async()=>{{{match.group('loaded')}"
        f"{match.group('before_initialize')}_ccProviderApplyWorkerFinal()"
        f"{match.group('before_initialize')[-1]}"
        f"await {match.group('initialize')}()"
    )


def _replace_provider_constructor(match: re.Match[str]) -> str:
    return (
        f'constructor({match.group("job")},{match.group("spawn")},'
        f'{match.group("auth")},{match.group("via")},{match.group("record")}{match.group("native_tail")},'
        f'_ccProviderEnv){{this.providerEnv={match.group("via")}==="adopted"&&'
        '_ccProviderEnv===void 0?null:_ccProviderRetain(_ccProviderEnv);this.dispatch='
        f'{match.group("job")};'
    )


def _replace_provider_claimed_spare_frame(match: re.Match[str]) -> str:
    original = match.group(0)
    original = checked_replace(
        original,
        f'{match.group("auth")}{match.group("tail")}){{',
        f'{match.group("auth")}{match.group("tail")},_ccProviderEnv){{',
        context="provider claimed-spare signature",
    )
    original = checked_replace(
        original,
        f'{match.group("options")});',
        f'{match.group("options")},_ccProviderEnv);',
        context="provider claimed-spare options",
    )
    return checked_replace(
        original,
        f'{match.group("spare")}.claimAuth))',
        f'{match.group("spare")}.claimAuth,_ccProviderEnv))',
        context="provider claimed-spare auth",
    )


def _replace_provider_manager_dispatch(match: re.Match[str]) -> str:
    original = checked_replace(
        match.group(0),
        ')=>',
        ',_ccProviderEnv)=>',
        context="provider manager dispatch signature",
    )
    return original + "_ccProviderEnv=_ccProviderRetain(_ccProviderEnv);"


def _replace_provider_stall_respawn(match: re.Match[str]) -> str:
    worker = match.group("worker")
    dispatch = match.group("dispatch")
    original = match.group(0)
    original = checked_replace(
        original,
        f'let {match.group("job")}={worker}.dispatch{match.group("separator")}',
        f'if({worker}._ccProviderBlocked())return;'
        f'let _ccProviderEnv={worker}.providerEnv;'
        f'let {match.group("job")}={worker}.dispatch{match.group("separator")}',
        context="provider attach-stall guard",
    )
    return checked_replace(
        original,
        f':{match.group("job")}.launch}})',
        f':{match.group("job")}.launch}},0,void 0{_provider_dispatch_padding(match.string)},_ccProviderEnv)',
        context=f"provider attach-stall {dispatch} snapshot",
    )


def _provider_binding_owner(source: str, offset: int, name: str) -> tuple[int, str]:
    modules = source_modules(source)
    owner = next(module for module in modules if module.start <= offset < module.end)
    for edge in re.finditer(r'import\{([^{}]*)\}from"([^"]+)"', owner.source):
        for binding in edge[1].split(","):
            pair = binding.strip().split(" as ")
            if pair[-1] != name:
                continue
            target = next(
                (module for module in modules if module.name == edge[2]), None
            )
            if target is None:
                continue
            for export in re.finditer(r'export\s*\{([^{}]*)\}', target.source):
                for item in export[1].split(","):
                    names = item.strip().split(" as ")
                    if names[-1] == pair[0]:
                        return _provider_binding_owner(source, target.start, names[0])
    return offset, name


def _provider_reference(source: str, offset: int, name: str, consumer: int) -> str:
    try:
        return module_reference(source, offset, name, consumer)
    except ModuleRuntimeError:
        modules = source_modules(source)
        target = next(m for m in modules if m.start <= offset < m.end)
        local = next(m for m in modules if m.start <= consumer < m.end)
        bindings = [
            item.strip().split(" as ")[-1]
            for edge in re.finditer(r'import\{([^{}]*)\}from"([^"]+)"', local.source)
            for item in edge[1].split(",")
        ]
        bindings.sort(
            key=lambda binding: not binding.startswith("__ccpatchNativeBinding")
        )
        for binding in bindings:
            root, root_name = _provider_binding_owner(source, consumer, binding)
            if target.start <= root < target.end and root_name == name:
                return binding
        raise


def _provider_ensure_reference(
    source: str, offset: int, name: str, consumer: int
) -> str:
    modules = source_modules(source)
    owner = next(m for m in modules if m.start <= offset < m.end)
    target = next(m for m in modules if m.start <= consumer < m.end)
    queue = [(target.name, [target.name])]
    seen: set[str] = set()
    path: list[str] | None = None
    while queue:
        current, route = queue.pop(0)
        if current == owner.name:
            path = route
            break
        if current in seen:
            continue
        seen.add(current)
        module = next(m for m in modules if m.name == current)
        for edge in re.finditer(r'import\{[^{}]*\}from"([^"]+)"', module.source):
            if any(m.name == edge[1] for m in modules):
                queue.append((edge[1], [*route, edge[1]]))
    if path is None:
        raise PatchError("provider native dependency path absent")
    for child, parent in zip(reversed(path[1:]), reversed(path[:-1]), strict=True):
        modules = source_modules(source)
        child_module = next(m for m in modules if m.name == child)
        parent_module = next(m for m in modules if m.name == parent)
        source, name = ensure_module_reference(
            source, child_module.start, name, parent_module.start
        )
    return source


def _provider_rv_protocol(source: str) -> str:
    return discover_identifiers(
        source,
        (re.compile(r'proto:(?P<proto>[\w$]+),role:"supervisor",supervisorPid:'),),
    )["proto"]


def _replace_provider_rv_worker(match: re.Match[str]) -> str:
    request = match.group("request")
    token = match.group("token")
    authenticated = match.group("authenticated")
    send = match.group("send") or "this.send"
    session_match = re.search(
        r'sessionId:(?P<session>[\w$]+)\(\),gates:\{', match.string
    )
    if session_match is None:
        raise PatchError("provider RV session getter absent")
    session = _provider_reference(
        match.string,
        *_provider_binding_owner(
            match.string, session_match.start(), session_match.group("session")
        ),
        match.start(),
    )
    validator = discover_identifiers(
        match.string,
        (
            re.compile(
                r'"auth"in (?P<auth_request>[\w$]+)(?:&&|\)if\()'
                r'(?P<validator>[\w$]+)\((?P=auth_request)\.auth,'
                + re.escape(token)
                + r'\)'
            ),
        ),
    )["validator"]
    protocol_match = re.search(
        r'proto:(?P<proto>[\w$]+),role:"supervisor",supervisorPid:', match.string
    )
    if protocol_match is None:
        raise PatchError("provider RV protocol absent")
    protocol = _provider_reference(
        match.string,
        *_provider_binding_owner(
            match.string, protocol_match.start(), protocol_match.group("proto")
        ),
        match.start(),
    )
    original = match.group(0)
    anchor = f'if({request}.type==="shutdown")'
    handler = (
        f'if({request}.type==="cc-provider-snapshot-request"){{'
        f'if(typeof {token}!=="string"||!{token}||{authenticated}!==!0||'
        f'!{validator}({request}.auth,{token})||{request}.proto!=={protocol}||'
        f'{request}.version!==3||{request}.sessionId!=={session}()||'
        f'typeof {request}.nonce!=="string"||!/^[a-f0-9]{{64}}$/.test({request}.nonce))return;'
        'try{let _ccReply={type:"cc-provider-snapshot",'
        f'proto:{protocol},version:3,sessionId:{session}(),nonce:{request}.nonce,'
        'payload:_ccProviderWorkerEnv};'
        f'_ccReply.mac=_ccProviderMac({token},_ccReply);{send}(_ccReply)'
        '}catch{}return}'
    )
    return checked_replace(
        original, anchor, handler + anchor, context="provider RV worker request"
    )


def _replace_provider_lifecycle(match: re.Match[str]) -> str:
    original = match.group(0)
    protocol_match = re.search(
        r'proto:(?P<proto>[\w$]+),role:"supervisor",supervisorPid:', match.string
    )
    if protocol_match is None:
        raise PatchError("provider RV protocol absent")
    protocol = _provider_reference(
        match.string,
        *_provider_binding_owner(
            match.string, protocol_match.start(), protocol_match.group("proto")
        ),
        match.start(),
    )

    def replace(old: str, new: str) -> None:
        nonlocal original
        original = checked_replace(original, old, new, context="provider lifecycle")

    methods = (
        '_ccProviderWarn(){if(this._ccProviderWarned)return;this._ccProviderWarned=!0;'
        'console.error("[bg] Original provider snapshot unavailable. Live session retained; '
        'automatic respawn disabled. Re-dispatch from the original provider session when ready.")}'
        '_ccProviderBlocked(){if(this.dispatch.launch.mode==="exec"||this.providerEnv!=null)'
        'return!1;this._ccProviderWarn();return!0}'
        '_ccProviderCancel(){clearTimeout(this._ccProviderTimer);'
        'this._ccProviderTimer=void 0;this._ccProviderNonce=void 0}'
        '_ccProviderRequest(){this._ccProviderCancel();'
        'if(this.providerEnv!=null||this.dispatch.launch.mode==="exec")return;'
        'if(typeof this.rvAuth!=="string"||!this.rvAuth){this._ccProviderWarn();return}'
        'let _ccNonce=require("crypto").randomBytes(32).toString("hex");'
        'this._ccProviderNonce=_ccNonce;'
        'this._ccProviderTimer=setTimeout(()=>{if(this._ccProviderNonce!==_ccNonce)return;'
        'this._ccProviderCancel();this._ccProviderWarn()},5000);this._ccProviderTimer.unref();'
        f'this.rv?.send({{type:"cc-provider-snapshot-request",proto:{protocol},version:3,'
        'sessionId:this.record.sessionId,nonce:_ccNonce,auth:this.rvAuth})}'
        '_ccProviderReply(_ccReply){if(_ccReply.type!=="cc-provider-snapshot")return!1;'
        'if(!this._ccProviderNonce||_ccReply.nonce!==this._ccProviderNonce)return!0;'
        f'if(_ccReply.proto!=={protocol}||_ccReply.version!==3||'
        '_ccReply.sessionId!==this.record.sessionId)return!0;'
        'try{if(typeof _ccReply.mac!=="string"||!/^[a-f0-9]{64}$/.test(_ccReply.mac))return!0;'
        'let _ccExpected=_ccProviderMac(this.rvAuth,_ccReply);'
        'if(!require("crypto").timingSafeEqual(Buffer.from(_ccExpected,"hex"),'
        'Buffer.from(_ccReply.mac,"hex")))return!0;'
        'this._ccProviderCancel();this.providerEnv=_ccProviderRetain(_ccReply.payload)'
        '}catch{this._ccProviderWarn()}return!0}'
    )
    replace('connectRv(){', methods + 'connectRv(){')
    callback = re.search(
        r'\((?P<event>[\w$]+)\)=>\{if\((?P=event)\.type==="heartbeat"\)', original
    )
    if callback is None:
        raise PatchError("provider lifecycle: RV callback absent")
    event = callback.group("event")
    replace(
        callback.group(0),
        f'({event})=>{{if(this._ccProviderReply({event}))return;if({event}.type==="heartbeat")',
    )
    replace(
        '()=>void this.checkPid(),()=>{',
        '()=>{this._ccProviderCancel();void this.checkPid()},()=>{'
        'this._ccProviderCancel();queueMicrotask(()=>{if(this.rv)this._ccProviderRequest()});',
    )
    replace('clearLiveness(){', 'clearLiveness(){this._ccProviderCancel();')
    original, count = re.subn(
        r'(async respawnIfIdleStale\([^)]*\)\{)',
        r'\1if(this._ccProviderBlocked())return{respawned:!1,reason:"provider-unavailable"};',
        original,
    )
    if count != 1:
        raise PatchError("provider lifecycle: stale respawn guard absent")
    original, count = re.subn(
        r'(async retireIfSettled\([^)]*\)\{)',
        r'\1if(this._ccProviderBlocked())return{retired:!1,reason:"provider-unavailable"};',
        original,
    )
    if count != 1:
        raise PatchError("provider lifecycle: retirement guard absent")
    for method in ("fireAuthRekey", "rekeyForAuthMismatch"):
        original = re.sub(
            rf'({method}\([^)]*\)\{{)',
            r'\1if(this._ccProviderBlocked())return;',
            original,
        )
    original, count = re.subn(
        r'(async doSpawn\([^)]*\)\{)',
        r'\1if(this._ccProviderBlocked())return;',
        original,
    )
    if count != 1:
        raise PatchError("provider lifecycle: spawn guard absent")
    original, count = re.subn(
        r'(onExit\((?P<code>[\w$]+),(?P<signal>[\w$]+)(?:,(?P<host_stderr>[\w$]+))?\)\{if\(this.isDetached\)return;if\(this.phase.kind==="retired"\)return;)',
        r'\1if(this._ccProviderBlocked()&&this.phase.kind!=="retiring")return this.settle(\g<code>===0?"done":"crashed");',
        original,
    )
    if count != 1:
        raise PatchError("provider lifecycle: exit guard absent")
    original, count = re.subn(
        r'(?P<worker>[\w$]+)\.connectRv\(\),(?P<roster>[\w$]+)\.pendingRespawn==="upgrade"',
        r'\g<worker>.connectRv(),\g<roster>.pendingRespawn==="upgrade"&&!\g<worker>._ccProviderBlocked()',
        original,
    )
    if count != 1:
        raise PatchError("provider lifecycle: pending upgrade guard absent")
    return original


BACKGROUND_PROVIDER_ENV = PatchSet(
    name="background-provider-environment",
    patches=(
        Patch(
            "snapshot-transient-provider-env",
            _PROVIDER_ENV_SNAPSHOT,
            _replace_provider_snapshot,
        ),
        Patch(
            "stop-persisting-provider-env",
            _PROVIDER_ENV_PERSISTED_DEFAULT,
            lambda match: (
                f'{match.group("isolation")}={match.group("default")}{match.group("source")}==="repl"?'
                f'"none":{match.group("options")}?.bgIsolation,'
                f'{match.group("provider")}=void 0,'
            ),
        ),
        Patch(
            "remove-provider-env-from-state-writes",
            _PROVIDER_ENV_STATE_WRITE,
            lambda match: checked_replace(
                match.group(0),
                f'...{match.group("rest")}',
                f'providerEnv:_ccProviderEnv,...{match.group("rest")}',
                context="provider state-write rest binding",
            ),
        ),
        Patch(
            "remove-provider-env-from-state-view",
            _PROVIDER_ENV_STATE_VIEW,
            "bgIsolation:" + r"\g<job>" + ".bgIsolation,",
        ),
        Patch("remove-provider-env-from-state-schema", _PROVIDER_ENV_STATE_SCHEMA, ""),
        Patch("remove-provider-env-from-job-copy", _PROVIDER_ENV_JOB_COPY, ""),
        Patch(
            "remove-provider-env-from-seed-state",
            _PROVIDER_ENV_SEED_STATE,
            "sessionPermissionRules:",
        ),
        Patch(
            "remove-provider-env-from-respawn-guard", _PROVIDER_ENV_RESPAWN_GUARD, ""
        ),
        Patch(
            "remove-provider-env-from-respawn-options", _PROVIDER_ENV_RESPAWN_OPTION, ""
        ),
        Patch(
            "add-control-provider-env", _PROVIDER_ENV_SCHEMA, _replace_provider_schema
        ),
        Patch(
            "send-provider-env-over-socket",
            _PROVIDER_ENV_SOCKET,
            _replace_provider_socket,
            expected_matches=(2,),  # Primary dispatch and timeout recovery.
        ),
        Patch(
            "reject-stale-daemon-without-file-fallback",
            _PROVIDER_ENV_SOCKET_RESULT,
            _replace_provider_socket_result,
        ),
        Patch(
            "validate-ack-timeout-redispatch",
            _PROVIDER_ENV_REDISPATCH_RESULT,
            _replace_provider_redispatch_result,
        ),
        Patch(
            "acknowledge-provider-env-version",
            _PROVIDER_ENV_DAEMON_ACK,
            _replace_provider_daemon_ack,
            expected_matches=(2,),  # Both daemon dispatch acknowledgement paths.
        ),
        Patch(
            "pass-provider-env-to-manager",
            _PROVIDER_ENV_CONTROL,
            _replace_provider_control,
        ),
        Patch(
            "declare-worker-provider-env",
            _PROVIDER_ENV_MANAGER,
            lambda match: checked_replace(
                match.group(0),
                "dispatch;",
                "dispatch;providerEnv;",
                context="provider worker field declaration",
            ),
        ),
        Patch(
            "store-worker-provider-env",
            _PROVIDER_ENV_CONSTRUCTOR,
            _replace_provider_constructor,
        ),
        Patch(
            "thread-provider-env-through-spawn",
            _PROVIDER_ENV_STATIC_SPAWN,
            lambda match: checked_replace(
                checked_replace(
                    match.group(0),
                    f'{match.group("options")}{match.group("tail")}){{',
                    f'{match.group("options")}{match.group("tail")},_ccProviderEnv){{',
                    context="provider static spawn signature",
                ),
                f'"cold"{match.group("constructor_tail")})',
                f'"cold"{match.group("constructor_tail") or ",void 0"},_ccProviderEnv)',
                context="provider static spawn constructor",
            ),
        ),
        Patch(
            "thread-provider-env-through-claim",
            _PROVIDER_ENV_STATIC_CLAIM,
            lambda match: (
                f'static claim({match.group("job")},{match.group("options")},_ccProviderEnv){{let {match.group("worker")}=new {match.group("class_name")}({match.group("job")},{match.group("options")}.spawnPty,{match.group("options")}.getAuthSnapshot,"spare",{match.group("record")}{match.group("tail")},_ccProviderEnv);'
            ),
        ),
        Patch(
            "apply-provider-env-to-claim-frame",
            _PROVIDER_ENV_CLAIM_FRAME,
            lambda match: checked_replace(
                match.group(0),
                f'{match.group("auth")})',
                f'{match.group("auth")},_ccProviderEnv)',
                context="provider claim-frame signature and call",
                count=2,
            ),
        ),
        Patch(
            "apply-provider-env-to-respawns",
            _PROVIDER_ENV_DO_SPAWN,
            lambda match: checked_replace(
                match.group(0),
                "this.socketAuth());",
                "this.socketAuth(),this.providerEnv);",
                context="provider worker respawn auth",
            ),
        ),
        Patch(
            "thread-provider-env-through-claimed-spare-frame",
            _PROVIDER_ENV_CLAIMED_SPARE_FRAME,
            _replace_provider_claimed_spare_frame,
        ),
        Patch(
            "thread-provider-env-to-claim-frame",
            _PROVIDER_ENV_BUILD_CLAIM_CALL,
            lambda match: checked_replace(
                checked_replace(
                    match.group(0),
                    f'{match.group("claim_auth")}){{',
                    f'{match.group("claim_auth")},_ccProviderEnv){{',
                    context="provider claim-frame wrapper signature and call",
                ),
                f'{match.group("auth")});',
                f'{match.group("auth")},_ccProviderEnv);',
                context="provider claim-frame wrapper signature and call",
            ),
        ),
        Patch(
            "accept-transient-provider-env-in-manager",
            _PROVIDER_ENV_MANAGER_DISPATCH,
            _replace_provider_manager_dispatch,
        ),
        Patch(
            "retain-provider-env-while-worker-settles",
            _PROVIDER_ENV_MANAGER_RETRY,
            lambda match: match.group(0)[:-1] + ",_ccProviderEnv)",
        ),
        Patch(
            "pass-provider-env-to-claimed-spare",
            _PROVIDER_ENV_MANAGER_CLAIM,
            lambda match: f'{match.group(0)[:-1]},_ccProviderEnv)',
        ),
        Patch(
            "pass-provider-env-to-cold-worker",
            _PROVIDER_ENV_MANAGER_SPAWN,
            lambda match: f'{match.group(0)[:-1]},_ccProviderEnv)',
        ),
        Patch(
            "scrub-worker-provider-env", _PROVIDER_ENV_WORKER, _replace_provider_worker
        ),
        Patch(
            "restore-provider-env-after-native-worker-scrubs",
            _PROVIDER_ENV_WORKER_FINAL,
            _replace_provider_worker_final,
        ),
        Patch(
            "apply-provider-env-after-claimed-initializers",
            _PROVIDER_ENV_CLAIMED_ENTRY,
            _replace_provider_claimed_entry,
        ),
        Patch(
            "capture-worker-provider-env-before-settings",
            _PROVIDER_ENV_PREACTION_START,
            _replace_provider_preact_start,
        ),
        Patch(
            "restore-provider-env-after-settings-initializer",
            _PROVIDER_ENV_PREACTION_INITIALIZED,
            _replace_provider_preact_initialized,
        ),
        Patch(
            "restore-provider-env-at-operational-entry",
            _PROVIDER_ENV_OPERATIONAL_ENTRY,
            _replace_provider_operational_entry,
        ),
        Patch(
            "restore-provider-env-after-delayed-settings",
            _PROVIDER_ENV_DELAYED_SETTINGS,
            _replace_provider_delayed_settings,
        ),
        Patch(
            "preserve-provider-on-attach-stall-respawn",
            re.compile(
                r'function [\w$]+\((?P<worker>[\w$]+),[\w$]+,'
                r'(?P<dispatch>[\w$]+),[\w$]+(?:,[\w$]+){0,2}\)\{'
                r'let (?P<job>[\w$]+)=(?P=worker)\.dispatch(?P<separator>[;,])'
                r'[^\n]*?attachStallRespawns:[^\n]*?:(?P=job)\.launch\}\)'
            ),
            _replace_provider_stall_respawn,
        ),
        Patch(
            "ignore-replaced-worker-rv-socket-data",
            re.compile(
                r'(?P<prefix>\.createServer\(\((?P<socket>[\w$]+)\)=>\{'
                r'(?P<current>[\w$]+)\?\.destroy\(\),[\s\S]*?'
                r'(?P=socket)\.on\("data",\([\w$]+\)=>\{)'
                r'|(?P<native>onConnection\((?P<native_socket>[\w$]+)\)\{this\.current\?\.destroy\(\),'
                r'this\.current=(?P=native_socket);?[\s\S]{0,2000}?'
                r'(?P=native_socket)\.on\("data",\([\w$]+\)=>\{)'
                r'(?P<native_guard>if\(this\.current!==(?P=native_socket)\)\{'
                r'(?P=native_socket)\.destroy\(\);return\})?'
            ),
            lambda match: (
                match.group("native")
                + (
                    match.group("native_guard")
                    or f'if(this.current!=={match.group("native_socket")})return;'
                )
                if match.group("native")
                else (
                    match.group("prefix")
                    + f'if({match.group("current")}!=={match.group("socket")})return;'
                )
            ),
        ),
        Patch(
            "serve-authenticated-provider-snapshot-on-worker-rv",
            re.compile(
                r'if\((?P<token>(?:this\.)?[\w$]+)&&!(?P<authenticated>(?:this\.)?[\w$]+)&&'
                r'(?P<request>[\w$]+)\.type!=="repaint"\)\{[^\n]*?'
                r'if\((?P=request)\.type==="shutdown"\)\{'
                r'(?:'
                r'[\w$]+\((?:this\.promptInput,)?this\.storageV5\);'
                r'|(?:'
                r'(?P<shutdown>[\w$]+)\(\);return\}'
                r'if\((?P=request)\.type==="repaint"\)\{[\w$]+\(\);return\}'
                r'if\((?P=request)\.type==="attacher-caps"\)\{'
                r'[\w$]+\((?P=request)\);return\}'
                r'if\((?P=request)\.type==="reply"&&'
                r'typeof (?P=request)\.text==="string"\)'
                r'[\w$]+\((?P=request)\)\}'
                r'function (?P=shutdown)\(\)\{'
                r')?'
                r'(?P<send>[\w$]+)\(\{type:"shutting-down"\}\);)'
            ),
            _replace_provider_rv_worker,
        ),
        Patch(
            "retain-adopted-provider-and-gate-unresolved-respawns",
            re.compile(
                r'class [\w$]+\{dispatch;providerEnv;[\s\S]*?clearLiveness\(\)\{[^}]+\}\}'
            ),
            _replace_provider_lifecycle,
        ),
    ),
    verify_present=(
        re.compile(r'function _ccProviderKeys\(\)\{if\('),
        re.compile(r'function _ccRequireProviderEnv\('),
        re.compile(r'function _ccProviderRetain\('),
        re.compile(r'let _ccProviderWorkerEnv=_ccProviderCaptureTransport\(\)'),
        re.compile(r'providerEnvVersion:3,providerEnv:[\w$]+\(\),timeoutMs:5000'),
        re.compile(r'op:[\w$]+,providerEnvVersion:3,short:'),
        re.compile(r'[\w$]+\.providerEnvVersion!==3'),
        re.compile(
            r'throw Object\.assign\(Error\([\w$]+\.error\),\{code:"EPROVIDERENV"\}\)'
        ),
        re.compile(r'"CLAUDE_CODE_CERT_STORE"'),
        re.compile(r'"VERTEX_REGION_CLAUDE_FABLE_5"'),
        re.compile(r'"VERTEX_REGION_CLAUDE_4_8_OPUS"'),
        re.compile(r'"VERTEX_REGION_CLAUDE_4_0_SONNET"'),
        re.compile(r'protocol mismatch\. Restart the stale Claude Code daemon'),
        re.compile(r'for\(let _ccKey of _ccProviderKeys\(\)\)delete process\.env'),
        re.compile(r'this\.providerEnv=[\w$]+==="adopted"&&'),
        re.compile(r'"cold",void 0(?:,[\w$]+(?:,[\w$]+)?)?,_ccProviderEnv'),
        re.compile(r'function _ccProviderMac\('),
        re.compile(r'_ccProviderReply\(_ccReply\)'),
        re.compile(r'providerEnv:_ccProviderEnv,\.\.\.[\w$]+'),
    ),
    verify_absent=(
        re.compile(r'providerEnv:[\w$]+\?\.providerEnv'),
        re.compile(r'providerEnv:[\w$]+\(\),sessionPermissionRules'),
        re.compile(r'\.providerEnv&&\{providerEnv:'),
        re.compile(r'providerEnv:[\w$]+\.record\([\w$]+\.string\(\),[\w$]+\.string'),
        re.compile(r'_ccProviderSnapshotFromEnv'),
    ),
    min_version=_V_2_1_174,
    max_version=(2, 1, 198),
    requires_version=True,
)


def _override_patches(
    patches: tuple[Patch, ...], overrides: dict[str, Patch]
) -> tuple[Patch, ...]:
    """Apply named overrides only when each target occurs exactly once."""
    for name, override in overrides.items():
        count = sum(patch.name == name for patch in patches)
        if count != 1 or override.name != name:
            raise PatchError(
                f"patch override {name!r}: expected one matching target, got {count}"
            )
    return tuple(overrides.get(patch.name, patch) for patch in patches)


_PROVIDER_ENV_198_MIN = (2, 1, 198)
_PROVIDER_ENV_198_MAX = (2, 1, 275)
_CLAIMED_SPARE_AUTH = r"(?P=job)\.short,(?P=auth)\?\.\(\)"
_CLAIMED_SPARE_AUTH_198 = rf"(?P=job)\.short,(?:{_ID}\((?P=job)\)\?void 0:(?P=auth)\?\.\(\)|{_ID}\((?P=job)\)\?(?P=auth)\?\.\(\):void 0)"


def _provider_env_198_overrides(patches: tuple[Patch, ...]) -> dict[str, Patch]:
    base = {patch.name: patch for patch in patches}
    return {
        "remove-provider-env-from-respawn-guard": replace(
            base["remove-provider-env-from-respawn-guard"],
            pattern=re.compile(
                rf"let\{{CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:{_ID},"
                rf"\.\.\.(?P<env>{_ID})\}}={_ID}\.providerEnv\?\?\{{\}};"
                rf"if\((?P<host>{_ID})\)for\(let (?P<key>{_ID}) of {_ID}\)"
                rf"delete (?P=env)\[(?P=key)\];let {_ID}=(?P=host)\?"
                rf'\{{\.\.\.(?P=env),CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:"1"\}}:'
                rf"(?P=env),"
            ),
            replacement=(
                'if(\\g<host>&&!["1","true","yes","on"].includes(String('
                'process.env.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST??"").toLowerCase().trim()))'
                'return{ok:!1,alive:!1,error:"Host-managed session requires a live host-managed provider context"};let '
            ),
        ),
        "stop-persisting-provider-env": replace(
            base["stop-persisting-provider-env"],
            replacement=lambda match: (
                f'{match.group("isolation")}={match.group("default")}{match.group("source")}==="repl"?'
                f'"none":{match.group("options")}?.bgIsolation,'
                f'{match.group("provider")}={{CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:'
                'process.env.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST},'
            ),
        ),
        "remove-provider-env-from-respawn-options": replace(
            base["remove-provider-env-from-respawn-options"],
            pattern=re.compile(
                rf'providerEnv:{_ID},(?=\.\.\.{_ID}\.sessionPermissionRules&&)'
            ),
        ),
        "thread-provider-env-through-claimed-spare-frame": replace(
            base["thread-provider-env-through-claimed-spare-frame"],
            pattern=re.compile(
                checked_replace(
                    _PROVIDER_ENV_CLAIMED_SPARE_FRAME.pattern,
                    _CLAIMED_SPARE_AUTH,
                    _CLAIMED_SPARE_AUTH_198,
                    context="2.1.198 claimed spare auth fragment",
                )
            ),
        ),
    }


# 2.1.198 removes the respawn guard and requires a live host provider context.
BACKGROUND_PROVIDER_ENV_198 = replace(
    BACKGROUND_PROVIDER_ENV,
    patches=_override_patches(
        BACKGROUND_PROVIDER_ENV.patches,
        _provider_env_198_overrides(BACKGROUND_PROVIDER_ENV.patches),
    ),
    verify_absent=(
        *BACKGROUND_PROVIDER_ENV.verify_absent,
        re.compile(rf"{_ID}\.providerEnv\?\?\{{\}}"),
    ),
    min_version=_PROVIDER_ENV_198_MIN,
    max_version=_PROVIDER_ENV_198_MAX,
)


_PROVIDER_ENV_AGENTS_SETTINGS = re.compile(
    rf'(?P<prefix>function {_ID}\(\)\{{(?:if\()?{_ID}\(\),'
    rf'.{{0,350}}?Object\.assign\(process\.env,{_ID}\({_ID}\(\)\.env,"globalConfig"\)\);'
    rf'for\(let .{{0,650}}?)(?P<record>{_ID}\({_ID}\))(?=[,;}}])'
)


def _initialize_provider_registry(match: re.Match[str]) -> str:
    """Initialize the native allowlist module before settings use its registry."""
    sources = re.findall(r"([\w$]+)==null", match.group(0))
    if not sources:
        raise PatchError("provider registry guard has no native key sources")
    assignments = list(
        re.finditer(rf"(?<![\w$.]){re.escape(sources[0])}=new Set\(", match.string)
    )
    if len(assignments) != 1:
        raise PatchError("provider allowlist initializer absent or ambiguous")
    modules = list(
        re.finditer(
            rf"var (?P<initialize>{_ID})={_ID}\(\(\)=>\{{",
            match.string[: assignments[0].start()],
        )
    )
    if not modules:
        raise PatchError("provider allowlist module initializer absent")
    module = modules[-1]
    prefix = match.string[module.end() : assignments[0].start()]
    # Accept only a flat module prefix. Fail closed on nested or closed scopes.
    if (
        re.fullmatch(r'''(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^{}"'`/])*''', prefix)
        is None
    ):
        raise PatchError("provider allowlist module scope is ambiguous")
    initialize = module.group("initialize")
    return match.group(0).replace("{if(", f"{{if(({initialize}(),", 1) + ")"


class _ProviderPatchSet(PatchSet):
    """Share provider capture and claim state across native module boundaries."""

    @typing_override
    def apply(self, source: str) -> str:
        for anchor in (
            r'sessionId:(?P<name>[\w$]+)\(\),gates:\{',
            r'proto:(?P<name>[\w$]+),role:"supervisor",supervisorPid:',
        ):
            definition = re.search(anchor, source)
            consumer = re.search(r'if\(this\.authToken&&!this\.currentAuthed&&', source)
            if definition is not None and consumer is not None:
                source = _provider_ensure_reference(
                    source,
                    *_provider_binding_owner(
                        source, definition.start(), definition.group("name")
                    ),
                    consumer.start(),
                )
        snapshot_definition = _PROVIDER_ENV_SNAPSHOT.search(source)
        fallback = _PROVIDER_ENV_AGENTS_FALLBACK.search(source)
        if snapshot_definition is not None and fallback is not None:
            source, _ = ensure_module_reference(
                source,
                snapshot_definition.start(),
                snapshot_definition.group("snapshot"),
                fallback.start(),
            )
        native_keys = _provider_serialized_keys(source)
        snapshot = _PROVIDER_ENV_SNAPSHOT.search(source)
        if snapshot is None:
            raise PatchError("provider snapshot absent")
        snapshot_name = snapshot.group("snapshot")
        source = super().apply(source)
        start = source.index("function _ccProviderKeys()")
        end = source.index(f"function {snapshot_name}()", start)
        code = source[start:end]
        key_end = code.index("function _ccRequireProviderEnv")
        code = "function _ccProviderKeys(){return " + native_keys + "}" + code[key_end:]
        source = source[:start] + source[end:]
        functions = re.findall(r"function (_cc(?:Provider|RequireProvider)\w*)\(", code)
        variables = (
            "_ccProviderPtyHost",
            "_ccProviderAwaitingClaim",
            "_ccProviderWorkerEnv",
            "_ccProviderCaptureReady",
            "_ccProviderInitialized",
        )
        names = (*functions, *variables)
        pattern = re.compile(r"(?<![\w$.])(" + "|".join(names) + r")(?![\w$])")
        source = pattern.sub(r"globalThis.__ccpatchRuntime.provider.\1", source)
        exports = [*functions]
        for name in variables:
            exports.extend(
                (f"get {name}(){{return {name}}}", f"set {name}(value){{{name}=value}}")
            )
        code += "return {" + ",".join(exports) + "};"
        return register_module_bootstrap(source, "provider", code)


def _provider_serialized_keys(source: str) -> str:
    """Resolve native constant lists without executing module initializers."""
    modules = source_modules(source)
    snapshot = _PROVIDER_ENV_SNAPSHOT.search(source)
    assert snapshot is not None
    snapshot_module = next(m for m in modules if m.start <= snapshot.start() < m.end)
    active: set[tuple[str, str]] = set()

    def resolve(name: str, module_name: str | None = None) -> list[str]:
        scope = next((m for m in modules if m.name == module_name), None)
        identity = (module_name or "", name)
        if identity in active:
            raise PatchError("cyclic provider key constants")
        active.add(identity)
        pattern = re.compile(
            rf"(?<![\w$.]){re.escape(name)}=(?:new Set\()?\[([^\[\]]*)\]"
        )
        matches = [
            (m, match)
            for m in modules
            if scope is None or m == scope
            for match in pattern.finditer(m.source)
        ]
        if scope is None:
            matches = [
                (m, match)
                for m, match in matches
                if re.search(r'"(?:ANTHROPIC_|CLAUDE_CODE_|AWS_|GOOGLE_)', match[1])
            ]
        if not matches and scope is not None:
            for edge in re.finditer(r'import\{([^{}]*)\}from"([^"]+)"', scope.source):
                for binding in edge[1].split(","):
                    pair = binding.strip().split(" as ")
                    if pair[-1] != name:
                        continue
                    target = next((m for m in modules if m.name == edge[2]), None)
                    if target is None:
                        continue
                    for exports in re.finditer(r"export\{([^{}]*)\}", target.source):
                        for binding in exports[1].split(","):
                            pair2 = binding.strip().split(" as ")
                            if pair2[-1] == pair[0]:
                                keys = resolve(pair2[0], target.name)
                                active.remove(identity)
                                return keys
        if len(matches) != 1:
            raise PatchError(f"provider key constant {name} absent or ambiguous")
        owner, match = matches[0]
        keys: list[str] = []
        for item in match[1].split(","):
            if not item:
                continue
            if item.startswith("..."):
                keys.extend(resolve(item[3:], owner.name))
            else:
                try:
                    key = json.loads(item)
                except ValueError as exc:
                    raise PatchError(f"nonconstant provider key {item}") from exc
                if not isinstance(key, str):
                    raise PatchError("nonstring provider key")
                keys.append(key)
        active.remove(identity)
        return keys

    sources = _provider_key_sources(source)
    keys = resolve(sources[0], snapshot_module.name)
    for name in sources[1:]:
        keys.extend(resolve(name))
    return json.dumps(list(dict.fromkeys((*keys, *_PROVIDER_ENV_EXPLICIT_KEYS))))


def _provider_env_207(base: PatchSet) -> PatchSet:
    """Reconcile requester routing inside the native settings boundary."""
    # Check raw policy before credentials enter the environment. Native filters can
    # remove conflicting routing keys, for example when a Unix socket is active.
    # Keep native probe markers and steering snapshots. Explicit requester defaults
    # are steering, not evidence that managed settings supplied those defaults.
    # The hoisted readiness flag protects settings calls before transport capture.
    obsolete = {
        "restore-provider-env-after-settings-initializer",
        "restore-provider-env-at-operational-entry",
        "restore-provider-env-after-delayed-settings",
    }
    patches = tuple(patch for patch in base.patches if patch.name not in obsolete)
    return _ProviderPatchSet(
        **{
            field: getattr(base, field)
            for field in (
                "name",
                "verify_present",
                "verify_absent",
                "min_version",
                "max_version",
                "requires_version",
            )
        },
        patches=(
            *patches,
            Patch(
                "initialize-provider-before-native-settings",
                re.compile(
                    rf'function (?P<apply>{_ID})\(\)\{{(?P<prefix>(?:if\()?(?:{_ID}\(\),){{1,3}})'
                    rf'(?=(?:{_ID}===void 0|{_ID}=\{{\}};let {_ID}={_ID}\.NODE_EXTRA_CA_CERTS))'
                    rf'(?=[\s\S]{{0,1000}}?Object\.assign\(process\.env,'
                    rf'{_ID}\((?P<settings>{_ID})\({_ID}\)\?\.env,{_ID}\)\))'
                    rf'|(?P<method>apply(?:Safe)?ConfigEnvironmentVariables\(\)\{{)'
                    rf'(?=[\s\S]{{0,2000}}?this\.filterSettingsEnv\((?P<method_settings>{_ID})\({_ID}\)\?\.env,{_ID}\))'
                ),
                lambda m: (
                    f'{m.group("method")}if(_ccProviderCaptureReady)_ccProviderInitialize({m.group("method_settings")}("policySettings")?.env);'
                    if m.group("method")
                    else f'function {m.group("apply")}(){{if(_ccProviderCaptureReady)'
                    f'_ccProviderInitialize({m.group("settings")}("policySettings")?.env);'
                    + m.group("prefix")
                ),
                expected_matches=(2,),
            ),
            Patch(
                "filter-provider-settings-with-native-policy",
                re.compile(
                    rf'function (?P<filter>{_ID})\((?P<env>{_ID}),(?P<scope>{_ID})\)'
                    rf'\{{return (?P<native>{_ID}\({_ID}\({_ID}\({_ID}\({_ID}\('
                    rf'(?:(?P=env)|{_ID}\((?P=env),(?P=scope)\))'
                    rf'\)\),(?P=scope)\)\)\))\}}'
                    rf'|filterSettingsEnv\((?P<method_env>{_ID}),(?P<method_scope>{_ID})\)'
                    rf'\{{return (?P<method_native>[^;{{}}]+)\}}'
                ),
                lambda m: (
                    f'filterSettingsEnv({m.group("method_env")},{m.group("method_scope")})'
                    f'{{_ccProviderValidateManaged({m.group("method_env")},{m.group("method_scope")});'
                    f'return _ccProviderFilterSettings({m.group("method_native")},{m.group("method_scope")})}}'
                    if m.group("method_env")
                    else f'function {m.group("filter")}({m.group("env")},{m.group("scope")})'
                    f'{{_ccProviderValidateManaged({m.group("env")},{m.group("scope")});'
                    f'return _ccProviderFilterSettings({m.group("native")},'
                    f'{m.group("scope")})}}'
                ),
            ),
            Patch(
                "preserve-provider-transport-in-pty-host",
                re.compile(
                    r"function _ccProviderCaptureTransport\(_ccEnv=process\.env\)\{"
                ),
                'function _ccProviderCaptureTransport(_ccEnv=process.env){'
                'if(process.argv[2]==="--bg-pty-host")return null;',
            ),
            Patch(
                "install-provider-native-policy-boundary",
                re.compile(
                    r'let _ccProviderWorkerEnv=_ccProviderCaptureTransport\(\);'
                ),
                'let _ccProviderPtyHost=process.argv[2]==="--bg-pty-host";'
                'let _ccProviderAwaitingClaim=process.argv[2]==="--bg-spare";'
                'let _ccProviderWorkerEnv=_ccProviderCaptureTransport();'
                'var _ccProviderCaptureReady=true;'
                'let _ccProviderInitialized=false;'
                'function _ccProviderInitialize(_ccPolicy){if(!_ccProviderCaptureReady||'
                '_ccProviderPtyHost||_ccProviderAwaitingClaim)return;'
                '_ccProviderValidateManaged(_ccPolicy,"policySettings");'
                'if(_ccProviderInitialized)return;'
                '_ccProviderApplyWorkerFinal();_ccProviderInitialized=true}'
                'function _ccProviderValidateManaged(_ccEnv,_ccScope){'
                'if(!_ccProviderCaptureReady||_ccProviderPtyHost||_ccProviderAwaitingClaim||_ccProviderWorkerEnv===null||'
                '_ccScope!=="policySettings")return;'
                'let _ccKeys=new Set(_ccProviderKeys());'
                'for(let[_ccKey,_ccValue]of Object.entries(_ccEnv??{}))'
                'if(_ccKeys.has(_ccKey)&&(_ccProviderWorkerEnv[_ccKey]??null)!==_ccValue)'
                'throw Object.assign(Error("Background requester provider conflicts with managed policy: "'
                '+_ccKey),{code:"EPROVIDERENV"})}'
                'function _ccProviderFilterSettings(_ccEnv,_ccScope){'
                'if(!_ccProviderCaptureReady||_ccProviderPtyHost||_ccProviderAwaitingClaim||_ccProviderWorkerEnv===null)return _ccEnv;'
                'let _ccKeys=new Set(_ccProviderKeys()),_ccOut={};'
                'for(let[_ccKey,_ccValue]of Object.entries(_ccEnv))'
                'if(!_ccKeys.has(_ccKey)||_ccScope==="policySettings")'
                '_ccOut[_ccKey]=_ccValue;return _ccOut}',
            ),
            Patch(
                "reset-provider-initialization-on-spare-claim",
                re.compile(
                    r'(?P<capture>_ccProviderWorkerEnv=_ccProviderCaptureTransport\([\w$]+\.env\);)'
                ),
                r'\g<capture>_ccProviderAwaitingClaim=false;_ccProviderInitialized=false;',
            ),
            Patch(
                "avoid-provider-reset-after-claimed-entry",
                re.compile(r'_ccProviderApplyWorkerFinal\(\);(?=await [\w$]+\(\)\})'),
                '',
            ),
            Patch(
                "carry-provider-env-to-agents-fallback",
                _PROVIDER_ENV_AGENTS_FALLBACK,
                _replace_provider_agents_fallback,
            ),
        ),
    )


def background_provider_environment(version: Version | None) -> PatchSet:
    """Include the agents fallback transport from 2.1.195 onwards."""
    base = (
        BACKGROUND_PROVIDER_ENV_198
        if version is not None and version >= (2, 1, 198)
        else BACKGROUND_PROVIDER_ENV
    )
    if version is not None and version >= (2, 1, 207):
        return _provider_env_207(base)
    if version is not None and version < (2, 1, 195):
        return base
    return replace(
        base,
        patches=(
            *base.patches,
            Patch(
                "retain-agents-provider-settings-authority",
                re.compile(
                    r'let _ccProviderWorkerEnv=_ccProviderCaptureTransport\(\);'
                ),
                'let _ccProviderWorkerEnv=_ccProviderCaptureTransport();'
                'const _ccAgentsProviderEnv=process.env.CLAUDE_AGENTS_SELECT'
                '?_ccProviderWorkerEnv:null;',
            ),
            Patch(
                "restore-agents-provider-after-settings-writes",
                _PROVIDER_ENV_AGENTS_SETTINGS,
                # Restore before native cache and network initialization. Do not
                # call settings from this hook or change untransported sessions.
                lambda m: (
                    m.group("prefix")
                    + '(_ccAgentsProviderEnv!==null'
                    + '?_ccProviderApplyFinal(_ccAgentsProviderEnv):void 0),'
                    + m.group("record")
                ),
                expected_matches=(2,),
            ),
            Patch(
                "carry-provider-env-to-agents-fallback",
                _PROVIDER_ENV_AGENTS_FALLBACK,
                _replace_provider_agents_fallback,
            ),
        ),
    )


# --- in-process multi-provider Anthropic SDK routing (2.1.174-2.1.200) --------

_MODEL_COSTS_RE = re.compile(
    r"((?:\},[\w$]+=[\w$]+;"
    r"(?:[\w$]+=new Set\([\w$]+\);)?|\bvar )[\w$]+="
    r"(?:Object\.assign\(Object\.create\(null\),)?\{)"
    r"(\[[\w$]+\([\w$]+\.firstParty\)\]:)"
)


def _model_costs_patch(model_costs: ModelCostsByModel) -> Patch:
    # Claude Code computes statusline/session cost from its own per-model table.
    # Claude Code lowercases model IDs before cost lookup. Use normalized qualified IDs.
    # Keep native entries and the catalogue spread last so native prices take precedence.
    # Do not add provider IDs to the native catalogue's validation set.
    table = ",".join(
        f"{json.dumps(model.lower(), separators=(',', ':'))}:"
        f"{{inputTokens:{costs['inputTokens']},"
        f"outputTokens:{costs['outputTokens']},"
        f"promptCacheWriteTokens:{costs['promptCacheWriteTokens']},"
        f"promptCacheReadTokens:{costs['promptCacheReadTokens']},"
        f"webSearchRequests:{costs['webSearchRequests']}}}"
        for model, costs in model_costs.items()
    )

    def repl(match: re.Match[str]) -> str:
        return f"{match.group(1)}{table},{match.group(2)}"

    return Patch(name="model-costs", pattern=_MODEL_COSTS_RE, replacement=repl)


_MULTI_PROVIDER_CATALOG_SOURCE: tuple[MultiProviderDefinition, ...] = (
    {
        "provider": "moonshot",
        "attributionDomain": "moonshot.ai",
        "baseURL": "https://api.kimi.com/coding",
        "tokenEnv": "CC_KIMI_AUTH_TOKEN",
        "defaultHeaders": {"User-Agent": "KimiCLI/1.5"},
        "models": (
            {
                "wireModel": "kimi-k3",
                "label": "Kimi K3",
                "description": "Moonshot general-purpose model",
                "contextWindow": 1_048_576,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 3,
                    "outputTokens": 15,
                    # Accounting assumption: charge cache writes at the ordinary
                    # input-token rate because Kimi publishes cache-hit/cache-miss
                    # prices rather than a separate cache-write price.
                    # Source: https://platform.kimi.ai/docs/pricing/chat-k3.md
                    "promptCacheWriteTokens": 3,
                    "promptCacheReadTokens": 0.3,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "kimi-k2.7-code",
                "label": "Kimi K2.7 Code",
                "description": "Moonshot coding model",
                "contextWindow": 262_144,
                "maxOutputTokens": 32_768,
                "costs": {
                    "inputTokens": 0.95,
                    "outputTokens": 4,
                    "promptCacheWriteTokens": 0.95,
                    "promptCacheReadTokens": 0.19,
                    "webSearchRequests": 0,
                },
            },
        ),
    },
    {
        "provider": "zai",
        "attributionDomain": "z.ai",
        "baseURL": "https://api.z.ai/api/anthropic",
        "tokenEnv": "CC_ZAI_AUTH_TOKEN",
        "defaultHeaders": {},
        "models": (
            {
                "wireModel": "glm-5.3",
                "label": "GLM 5.3",
                "description": "Z.ai flagship model",
                "contextWindow": 1_000_000,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 1.4,
                    "outputTokens": 4.4,
                    "promptCacheWriteTokens": 1.4,
                    "promptCacheReadTokens": 0.26,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-5.3-flash",
                "label": "GLM 5.3 Flash",
                "description": "Z.ai fast model",
                "contextWindow": 1_000_000,
                "maxOutputTokens": 131_072,
                # Use the standard rate effective on 2026-09-10, after the launch
                # promotion ends. Source: https://docs.z.ai/guides/overview/pricing
                "costs": {
                    "inputTokens": 0.15,
                    "outputTokens": 0.5,
                    "promptCacheWriteTokens": 0.15,
                    "promptCacheReadTokens": 0.03,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-5.2",
                "label": "GLM 5.2",
                "description": "Z.ai coding model",
                "contextWindow": 1_000_000,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 1.4,
                    "outputTokens": 4.4,
                    "promptCacheWriteTokens": 1.4,
                    "promptCacheReadTokens": 0.26,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-5-turbo",
                "label": "GLM 5 Turbo",
                "description": "Z.ai coding model",
                "contextWindow": 200_000,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 1.2,
                    "outputTokens": 4,
                    "promptCacheWriteTokens": 1.2,
                    "promptCacheReadTokens": 0.2,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-4.7",
                "label": "GLM 4.7",
                "description": "Z.ai coding model",
                "contextWindow": 204_800,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 0.6,
                    "outputTokens": 2.2,
                    "promptCacheWriteTokens": 0.6,
                    "promptCacheReadTokens": 0.11,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "glm-4.5-air",
                "label": "GLM 4.5 Air",
                "description": "Z.ai coding model",
                "contextWindow": 131_072,
                "maxOutputTokens": 98_304,
                "costs": {
                    "inputTokens": 0.2,
                    "outputTokens": 1.1,
                    "promptCacheWriteTokens": 0.2,
                    "promptCacheReadTokens": 0.03,
                    "webSearchRequests": 0,
                },
            },
        ),
    },
    {
        "provider": "minimax",
        "attributionDomain": "minimax.io",
        "baseURL": "https://api.minimax.io/anthropic",
        "tokenEnv": "CC_MINIMAX_AUTH_TOKEN",
        "defaultHeaders": {},
        "models": (
            {
                "wireModel": "MiniMax-M3",
                "label": "MiniMax M3",
                "description": "MiniMax coding model",
                "contextWindow": 1_048_576,
                "maxOutputTokens": 512_000,
                "costs": {
                    "inputTokens": 0.3,
                    "outputTokens": 1.2,
                    "promptCacheWriteTokens": 0.375,
                    "promptCacheReadTokens": 0.06,
                    "webSearchRequests": 0,
                },
            },
            {
                "wireModel": "MiniMax-M2.7",
                "label": "MiniMax M2.7",
                "description": "MiniMax coding model",
                "contextWindow": 204_800,
                "maxOutputTokens": 131_072,
                "costs": {
                    "inputTokens": 0.3,
                    "outputTokens": 1.2,
                    "promptCacheWriteTokens": 0.375,
                    "promptCacheReadTokens": 0.06,
                    "webSearchRequests": 0,
                },
            },
        ),
    },
    {
        "provider": "openai",
        "attributionDomain": "openai.com",
        "baseURL": "http://127.0.0.1:17780",
        "baseURLEnv": "CC_OPENAI_PROXY_EFFECTIVE_URL",
        "tokenEnv": "CC_OPENAI_PROXY_AUTH_TOKEN",
        "availabilityEnv": "CC_OPENAI_AVAILABLE",
        "defaultHeaders": {},
        "models": (
            {
                "wireModel": "gpt-6-astra",
                "label": "GPT-6 Astra",
                "description": "OpenAI Codex model",
                "contextWindow": 272_000,
                "maxOutputTokens": 128_000,
                "costs": {
                    "inputTokens": 10,
                    "outputTokens": 50,
                    "promptCacheWriteTokens": 12.5,
                    "promptCacheReadTokens": 1,
                    "webSearchRequests": 0.01,
                },
            },
            {
                "wireModel": "gpt-5.6-sol",
                "label": "GPT-5.6 Sol",
                "description": "OpenAI Codex model",
                "contextWindow": 272_000,
                "maxOutputTokens": 128_000,
                "costs": {
                    "inputTokens": 4,
                    "outputTokens": 20,
                    "promptCacheWriteTokens": 5,
                    "promptCacheReadTokens": 0.4,
                    "webSearchRequests": 0.01,
                },
            },
            {
                "wireModel": "gpt-5.6-terra",
                "label": "GPT-5.6 Terra",
                "description": "OpenAI Codex model",
                "contextWindow": 272_000,
                "maxOutputTokens": 128_000,
                "costs": {
                    "inputTokens": 2,
                    "outputTokens": 12,
                    "promptCacheWriteTokens": 2.5,
                    "promptCacheReadTokens": 0.2,
                    "webSearchRequests": 0.01,
                },
            },
            {
                "wireModel": "gpt-5.6-luna",
                "label": "GPT-5.6 Luna",
                "description": "OpenAI Codex model",
                "contextWindow": 272_000,
                "maxOutputTokens": 128_000,
                "costs": {
                    "inputTokens": 0.2,
                    "outputTokens": 1.2,
                    "promptCacheWriteTokens": 0.25,
                    "promptCacheReadTokens": 0.02,
                    "webSearchRequests": 0.01,
                },
            },
        ),
    },
)
_MULTI_PROVIDER_DEFINITIONS = json.dumps(
    {
        definition["provider"]: {
            "attributionDomain": definition["attributionDomain"],
            "baseURL": definition["baseURL"],
            "tokenEnv": definition["tokenEnv"],
            **(
                {"availabilityEnv": definition["availabilityEnv"]}
                if "availabilityEnv" in definition
                else {}
            ),
            **(
                {"baseURLEnv": definition["baseURLEnv"]}
                if "baseURLEnv" in definition
                else {}
            ),
            "defaultHeaders": definition["defaultHeaders"],
            "models": [model["wireModel"] for model in definition["models"]],
        }
        for definition in _MULTI_PROVIDER_CATALOG_SOURCE
    },
    separators=(",", ":"),
)
_MULTI_PROVIDER_PREFIXES = json.dumps(
    [definition["provider"] for definition in _MULTI_PROVIDER_CATALOG_SOURCE],
    separators=(",", ":"),
)
_MULTI_PROVIDER_CATALOG = json.dumps(
    [
        {
            "value": f'{definition["provider"]}:{model["wireModel"]}',
            "label": model["label"],
            "attributionDomain": definition["attributionDomain"],
            "description": model["description"],
            "contextWindow": model["contextWindow"],
            "maxOutputTokens": model["maxOutputTokens"],
        }
        for definition in _MULTI_PROVIDER_CATALOG_SOURCE
        for model in definition["models"]
    ],
    separators=(",", ":"),
)
_MULTI_PROVIDER_MODEL_COSTS: ModelCostsByModel = {
    f'{definition["provider"]}:{model["wireModel"]}': model["costs"]
    for definition in _MULTI_PROVIDER_CATALOG_SOURCE
    for model in definition["models"]
}
# Keep saved qualified IDs billable without adding duplicate picker entries.
_MULTI_PROVIDER_MODEL_COSTS.update(
    {
        model.replace("moonshot:", "kimi:", 1): costs
        for model, costs in _MULTI_PROVIDER_MODEL_COSTS.items()
        if model.startswith("moonshot:")
    }
)
_MULTI_PROVIDER_HELPER = (
    f"const _ccMultiProviderDefinitions={_MULTI_PROVIDER_DEFINITIONS};"
    f"const _ccMultiProviderCatalog={_MULTI_PROVIDER_CATALOG};"
    f"const _ccMultiProviderPrefixes={_MULTI_PROVIDER_PREFIXES},"
    '_ccMultiProviderDeniedRequestFields=["fallback_credit_token"],'
    '_ccMultiProviderTraceHeaders=["traceparent","tracestate","baggage"],'
    "_ccMultiProviderClients=new Map;"
    'function _ccMultiProviderCanonicalModel(_ccModel){return typeof _ccModel==="string"'
    '&&_ccModel.startsWith("kimi:")?"moonshot:"+_ccModel.slice(5):_ccModel}'
    "function _ccMultiProviderCatalogInfo(_ccModel){if(typeof _ccModel!==\"string\")"
    "return null;_ccModel=_ccMultiProviderCanonicalModel(_ccModel);"
    "return _ccMultiProviderCatalog.find((_ccEntry)=>_ccEntry.value==="
    "_ccModel)??null}"
    "function _ccMultiProviderAttribution(_ccModel,_ccNativeLabel){let _ccEntry="
    "_ccMultiProviderCatalogInfo(_ccModel);return{label:_ccEntry?.label??_ccNativeLabel,"
    'domain:_ccEntry?.attributionDomain??"anthropic.com"}}'
    "function _ccMultiProviderModelProvider(_ccModel){if(typeof _ccModel!==\"string\")"
    'return"anthropic";_ccModel=_ccMultiProviderCanonicalModel(_ccModel.toLowerCase());'
    'let _ccSeparator=_ccModel.indexOf(":"),_ccPrefix='
    "_ccSeparator<0?null:_ccModel.slice(0,_ccSeparator).toLowerCase();if(_ccPrefix&&"
    "_ccMultiProviderPrefixes.includes(_ccPrefix))return _ccPrefix;let _ccKnown="
    "_ccMultiProviderCatalog.find((_ccEntry)=>_ccEntry.value.slice("
    "_ccEntry.value.indexOf(\":\")+1).toLowerCase()===_ccModel.toLowerCase());return "
    '_ccKnown?_ccKnown.value.slice(0,_ccKnown.value.indexOf(":")):"anthropic"}'
    "function _ccMultiProviderModelError(_ccMessage){return Object.assign(Error("
    '_ccMessage),{code:"EPROVIDERMODEL"})}'
    "function _ccMultiProviderModelInfo(_ccModel){if(typeof _ccModel!==\"string\")"
    "return null;_ccModel=_ccMultiProviderCanonicalModel(_ccModel);"
    "let _ccSeparator=_ccModel.indexOf(\":\"),_ccProvider="
    "_ccSeparator<0?null:_ccModel.slice(0,_ccSeparator),_ccWireModel="
    "_ccSeparator<0?_ccModel:_ccModel.slice(_ccSeparator+1),_ccKnownWire="
    "_ccMultiProviderCatalog.find((_ccEntry)=>_ccEntry.value.slice("
    "_ccEntry.value.indexOf(\":\")+1).toLowerCase()===_ccWireModel.toLowerCase()),"
    "_ccCanonicalProvider=_ccProvider===null?null:_ccMultiProviderPrefixes.find("
    "(_ccPrefix)=>_ccPrefix===_ccProvider.toLowerCase());if(_ccProvider===null){if("
    "_ccKnownWire)throw _ccMultiProviderModelError(\"External model requires a "
    "provider prefix: \"+_ccModel);return null}if(_ccCanonicalProvider&&_ccProvider!=="
    "_ccCanonicalProvider)throw _ccMultiProviderModelError(\"Provider prefix must be "
    "lowercase: \"+_ccProvider);if(!_ccCanonicalProvider){if(_ccKnownWire)throw "
    "_ccMultiProviderModelError(\"External model has an unknown provider prefix: \"+"
    "_ccModel);return null}if(!_ccWireModel.trim())throw _ccMultiProviderModelError("
    '"Invalid qualified model: wire model is blank");let _ccDefinition='
    "_ccMultiProviderDefinitions[_ccCanonicalProvider];if(!_ccDefinition.models.includes("
    "_ccWireModel))throw _ccMultiProviderModelError(\"Unknown qualified model: \"+"
    "_ccCanonicalProvider+\":\"+_ccWireModel);return{provider:"
    "_ccCanonicalProvider,wireModel:_ccWireModel,definition:_ccDefinition}}"
    "function _ccMultiProviderAvailable(_ccProvider){let _ccDefinition="
    "_ccMultiProviderDefinitions[_ccProvider],_ccToken=process.env["
    "_ccDefinition.tokenEnv]?.trim(),_ccBaseURL=_ccDefinition.baseURLEnv?process.env["
    "_ccDefinition.baseURLEnv]?.trim():_ccDefinition.baseURL;return!!_ccToken&&"
    "!!_ccBaseURL&&(!_ccDefinition.availabilityEnv||process.env["
    '_ccDefinition.availabilityEnv]==="1")}function '
    "_ccMultiProviderPickerCatalog(){return _ccMultiProviderCatalog.filter("
    "(_ccEntry)=>_ccMultiProviderAvailable(_ccEntry.value.slice(0,"
    '_ccEntry.value.indexOf(":"))))}'
    "function _ccMultiProviderToolAllowed(_ccModel,_ccTool){return _ccTool.isMcp===!0||"
    '_ccTool.name!=="WebSearch"||_ccMultiProviderModelProvider(_ccModel)==="anthropic"}'
    "function _ccMultiProviderSafeFetchOptions(_ccOptions){if(!_ccOptions)return "
    "_ccOptions;let{headers:_ccHeaders,..._ccSafe}=_ccOptions;return _ccSafe}"
    "function _ccMultiProviderSafeOptions(_ccOptions){let _ccSafe={};if("
    'Object.hasOwn(_ccOptions,"signal"))_ccSafe.signal=_ccOptions.signal;if('
    'Object.hasOwn(_ccOptions,"timeout"))_ccSafe.timeout=_ccOptions.timeout;let '
    "_ccHeaders={};for(let[_ccName,_ccValue]of Object.entries(_ccOptions.headers??{}))"
    "if(_ccMultiProviderTraceHeaders.includes(_ccName.toLowerCase()))"
    "_ccHeaders[_ccName]=_ccValue;if(Object.keys(_ccHeaders).length)_ccSafe.headers="
    "_ccHeaders;return _ccSafe}"
    "function _ccMultiProviderCredential(_ccInfo){let _ccToken=process.env["
    "_ccInfo.definition.tokenEnv]?.trim(),_ccBaseURL=_ccInfo.definition.baseURLEnv?"
    "process.env[_ccInfo.definition.baseURLEnv]?.trim():_ccInfo.definition.baseURL;if("
    "!_ccToken||!_ccBaseURL||(_ccInfo.definition.availabilityEnv&&process.env["
    "_ccInfo.definition.availabilityEnv]!==\"1\")){_ccMultiProviderClients.delete("
    "_ccInfo.provider);let _ccMissing=!_ccToken?_ccInfo.definition.tokenEnv:!_ccBaseURL?"
    "_ccInfo.definition.baseURLEnv:_ccInfo.definition.availabilityEnv;throw Object.assign("
    "Error(\"Missing provider configuration: \"+_ccMissing),"
    '{code:"EPROVIDERCREDENTIAL"})}return{token:_ccToken,baseURL:_ccBaseURL}}'
    "function _ccMultiProviderPreflight(_ccModel){let _ccInfo="
    "_ccMultiProviderModelInfo(_ccModel);if(_ccInfo)_ccMultiProviderCredential(_ccInfo)}"
    "function _ccMultiProviderInputTokens(_ccModel,_ccResponse){if("
    "_ccMultiProviderModelInfo(_ccModel)&&typeof _ccResponse?.input_tokens!==\"number\")"
    "throw Object.assign(Error(\"External provider countTokens response must contain "
    'numeric input_tokens"),{code:"EPROVIDERINCOMPATIBLE"});return '
    "_ccResponse?.input_tokens}"
    # Move Moonshot tool additions to the tool list. References keep full definitions.
    "function _ccMultiProviderMoonshotRequest(_ccRequest){if(!Array.isArray("
    "_ccRequest.messages))return _ccRequest;let _ccTools=new Map((_ccRequest.tools??[])"
    ".map((_ccTool)=>[_ccTool.name,_ccTool])),_ccReferences=new Set,_ccChanged=!1,"
    "_ccMessages=[];for(let _ccMessage of _ccRequest.messages){if(!Array.isArray("
    "_ccMessage.content)){_ccMessages.push(_ccMessage);continue}let _ccContent=[],"
    "_ccRemoved=!1;for(let _ccBlock of _ccMessage.content){if(_ccBlock.type!=="
    '"tool_addition"){_ccContent.push(_ccBlock);continue}_ccChanged=!0;_ccRemoved=!0;'
    "let _ccTool=_ccBlock.tool;if(!_ccTool||typeof _ccTool.name!==\"string\"||"
    "!_ccTool.name)throw Object.assign(Error(\"Invalid Moonshot tool addition\"),"
    '{code:"EPROVIDERINCOMPATIBLE"});if(_ccTool.type==="tool_reference")'
    "_ccReferences.add(_ccTool.name);else _ccTools.set(_ccTool.name,_ccTool)}if("
    "!_ccRemoved)_ccMessages.push(_ccMessage);else if(_ccContent.length)"
    "_ccMessages.push({..._ccMessage,content:_ccContent})}if(!_ccChanged)return "
    "_ccRequest;for(let _ccName of _ccReferences)if(!_ccTools.has(_ccName)||"
    '_ccTools.get(_ccName).type==="tool_reference")throw Object.assign(Error('
    '"Missing Moonshot tool definition: "+_ccName),{code:"EPROVIDERINCOMPATIBLE"});'
    "return{..._ccRequest,messages:_ccMessages,tools:[..._ccTools.values()]}}"
    "function _ccMultiProviderRoute(_ccNativeClient,_ccRequest,_ccOptions={}){let "
    "_ccInfo=_ccMultiProviderModelInfo(_ccRequest.model);if(!_ccInfo)return["
    "_ccNativeClient,_ccRequest,_ccOptions];let{token:_ccToken,baseURL:_ccBaseURL}="
    "_ccMultiProviderCredential(_ccInfo),_ccCached=_ccMultiProviderClients.get("
    "_ccInfo.provider),_ccTransportChanged=!_ccCached||_ccCached.token!==_ccToken||"
    "_ccCached.baseURL!==_ccBaseURL||"
    "_ccCached.nativeClient!==_ccNativeClient||_ccCached.timeout!=="
    "_ccNativeClient.timeout||_ccCached.fetchOptions!==_ccNativeClient.fetchOptions||"
    "_ccCached.fetch!==_ccNativeClient.fetch;if(_ccTransportChanged){let "
    "_ccConstructor=_ccMultiProviderSDK(),_ccClient=new _ccConstructor({baseURL:"
    "_ccBaseURL,apiKey:null,authToken:_ccToken,maxRetries:0,"
    "dangerouslyAllowBrowser:!0,timeout:_ccNativeClient.timeout,fetchOptions:"
    "_ccMultiProviderSafeFetchOptions(_ccNativeClient.fetchOptions),fetch:"
    "_ccNativeClient.fetch,defaultHeaders:{..."
    "_ccInfo.definition.defaultHeaders}});if(_ccClient._options)_ccClient._options={..."
    "_ccClient._options,defaultHeaders:{..._ccInfo.definition.defaultHeaders}};"
    "_ccCached={token:_ccToken,baseURL:_ccBaseURL,nativeClient:_ccNativeClient,timeout:"
    "_ccNativeClient.timeout,fetchOptions:_ccNativeClient.fetchOptions,fetch:"
    "_ccNativeClient.fetch,client:_ccClient};_ccMultiProviderClients.set("
    "_ccInfo.provider,_ccCached)}let _ccOutbound={..._ccRequest,model:"
    '_ccInfo.wireModel};if(_ccInfo.provider==="moonshot")_ccOutbound='
    "_ccMultiProviderMoonshotRequest(_ccOutbound);for(let _ccField of "
    "_ccMultiProviderDeniedRequestFields)delete _ccOutbound[_ccField];"
    "return[_ccCached.client,_ccOutbound,"
    "_ccMultiProviderSafeOptions(_ccOptions)]}"
)
_MULTI_PROVIDER_RESUME = re.compile(
    rf'let (?P<model>{_ID})=(?P<message>{_ID})\.message\.model,'
    rf'(?P<setting>{_ID})=(?P<current>{_ID})\(\)'
    rf'(?P<setting_normalization>,(?P<normalized_setting>{_ID})=typeof (?P=setting)==="string"\?'
    rf'{_ID}\((?P=setting)\):(?P=setting))?;if\('
    rf'(?P<dependent>{_ID})\((?(normalized_setting)(?P=normalized_setting)|(?P=setting))\)&&!(?P<eap>{_ID})\((?P=model)\)&&'
    rf'(?P<compatible>{_ID})\((?(normalized_setting)(?P=normalized_setting)|(?P=setting)),(?P<normalize>{_ID})\((?P=model)\)\)\)'
    r'return\{kind:"mode_dependent_setting"\};'
    rf'(?=let {_ID}=!\([^;]{{1,200}}\)\?"unknown_family":!'
    rf'(?:(?P<exempt>{_ID})\((?P=model)\)&&!)?'
    rf'(?P<allowed>{_ID})\((?P=model)\)\?"not_allowed")'
)
_MULTI_PROVIDER_AGENT_MODEL = re.compile(
    r'model:(?P<schema>[\w$]+)(?P<enum>\.enum)?\(\["sonnet","opus","haiku","fable"\]\)'
    r'(?=\.optional\(\)\.describe\(["`]Optional model override for this agent\.)'
)


def _restore_multi_provider_model(match: re.Match[str]) -> str:
    model = match.group("model")
    allowed = f"{match.group('allowed')}(_ccRestored)"
    if exempt := match.group("exempt"):
        allowed = f"{exempt}(_ccRestored)||{allowed}"
    # Only restore bare wire IDs when the catalogue has one matching provider.
    return (
        f"let {model}={match.group('message')}.message.model;"
        f"let _ccCandidates=_ccMultiProviderCatalog.filter((_ccEntry)=>"
        f"_ccEntry.value===_ccMultiProviderCanonicalModel({model})||_ccEntry.value.slice("
        f'_ccEntry.value.indexOf(":")+1)==={model});'
        "if(_ccCandidates.length===1){let _ccRestored=_ccCandidates[0].value;"
        f'return {allowed}?{{kind:"ok",model:_ccRestored}}:'
        '{kind:"declined",model:_ccRestored,reason:"not_allowed"}}'
        f"let {match.group('setting')}={match.group('current')}()"
        f"{match.group('setting_normalization') or ''};if("
        f"{match.group('dependent')}({match.group('normalized_setting') or match.group('setting')})&&!"
        f"{match.group('eap')}({model})&&{match.group('compatible')}("
        f"{match.group('normalized_setting') or match.group('setting')},{match.group('normalize')}({model})))"
        'return{kind:"mode_dependent_setting"};'
    )


_MULTI_PROVIDER_AGENT_IDENTIFIERS = (
    re.compile(
        rf'(?<![\w$.])(?P<aliases>{_ID})=\["sonnet","opus","haiku","fable",'
        r'"best","sonnet\[1m\]","opus\[1m\]","fable\[1m\]","opusplan"\]'
    ),
    re.compile(
        rf'(?<![\w$.])(?P<first_party>{_ID})=Object\.values\({_ID}\)'
        rf'\.map\(\((?P<entry>{_ID})\)=>(?P=entry)\.firstParty\)'
    ),
    re.compile(
        rf'(?:process\.env\.ANTHROPIC_DEFAULT_FABLE_MODEL\|\||'
        rf'function {_ID}\(({_ID})=)'
        rf'(?P<models>{_ID})\(\)'
        rf'(?(1)\)\{{let {_ID}=(?:{_ID}\("fable",\1\)\?\?)?\1\.fable5|\.fable5)'
    ),
    re.compile(
        rf'function (?P<picker>{_ID})\({_ID}=!1(?:,{_ID}=null)?\)\{{let {_ID}=new Set,'
        rf'{_ID}={_ID}\({_ID}(?:,{_ID})?\)\.filter\(\({_ID}\)=>\{{'
        rf'if\({_ID}\.value===null\)return!0;if\({_ID}\.has\({_ID}\.value\)\)'
        rf'(?:\{{if\(({_ID})!==null\)\2\.duplicates\+\+;)?'
        rf'return {_ID}\(`model options: dropping duplicate row '
    ),
)


def _expand_multi_provider_agent_model(
    match: re.Match[str], bindings: dict[str, str]
) -> str:
    return (
        f"model:{match.group('schema')}{match.group('enum') or ''}([...new Set([...{bindings['aliases']},"
        f"...{bindings['first_party']},...Object.values({bindings['models']}()),"
        f"...{bindings['picker']}().filter((_ccEntry)=>"
        'typeof _ccEntry.value==="string").map((_ccEntry)=>_ccEntry.value),'
        "..._ccMultiProviderCatalog.flatMap((_ccEntry)=>"
        '_ccEntry.value.startsWith("moonshot:")?[_ccEntry.value,'
        '_ccEntry.value.replace("moonshot:","kimi:")]:[_ccEntry.value])])])'
    )


_MULTI_PROVIDER_SDK_TAIL = re.compile(
    rf'(?P<prefix>let (?P<options>{_ID})=\{{apiKey:.{{0,500}}?\}};return new '
    rf'(?P<constructor>{_ID})\((?P=options)\)\}})(?P<next>async function {_ID}\()'
)
_MULTI_PROVIDER_THINKING_FILTER = re.compile(
    rf"function (?P<function>{_ID})\((?P<messages>{_ID}),(?P<model>{_ID})\)"
    rf"\{{return (?P<filter>{_ID})\((?P=messages),\((?P<message>{_ID})\)=>"
    rf"(?P=message)\.message\.model!==(?P<synthetic>{_ID})&&"
    rf"(?P=message)\.message\.model!==(?P=model)\)\}}"
    rf'|function (?P<predicate>{_ID})\((?P<item>{_ID}),(?P<target>{_ID})\)'
    rf'\{{let (?P<origin>{_ID})=(?P=item)\.message\.model;'
    rf'if\(typeof (?P=origin)!=="string"\)return!0;return '
    rf'(?P<native>(?P=origin)!==(?P<sentinel>{_ID})&&(?P=origin)!==(?P=target)&&'
    rf'{_ID}\((?P=origin)(?:,\{{identity:!0\}})?\)!=={_ID}\((?P=target)(?:,\{{identity:!0\}})?\)&&{_ID}\((?P=target)\)'
    rf'\?\.has\((?P=origin)\)!==!0)\}}'
)
# Release .200 passes the finalized request directly. Keep late EXTRA_BODY.model
# values literal; upstream resolves the selected model before the extra-body merge.
_MULTI_PROVIDER_NONSTREAMING = re.compile(
    rf'let (?P<response>{_ID})=await (?P<client>{_ID})\.beta\.messages\.create\('
    rf'(?P<request>\{{\.\.\.(?P<finalized>{_ID}),(?:model:(?P<normalize>[\w$]+)\((?P=finalized)\.model\)|stream:!1)\}}|{_ID}),'
    rf'(?P<options>\{{signal:(?P<signal>{_ID})\.signal,timeout:(?P<timeout>{_ID}),'
    rf'\.\.\.Object\.keys\((?P<headers>{_ID})\)\.length>0&&\{{headers:(?P=headers)\}}\}})'
)
_MULTI_PROVIDER_STREAMING = re.compile(
    rf'let (?P<response>{_ID})=await (?P<client>{_ID})\.beta\.messages\.create\('
    rf'(?P<request>\{{\.\.\.(?P<finalized>{_ID}),\.\.\.(?P<credit>{_ID})!==void 0&&'
    rf'\{{fallback_credit_token:(?P=credit)\}},stream:!0\}}),'
    rf'(?P<options>\{{signal:(?P<signal>{_ID}),\.\.\.Object\.keys\('
    rf'(?P<headers>{_ID})\)\.length>0&&\{{headers:(?P=headers)\}}\}})'
)
_MULTI_PROVIDER_SIDE_QUERY = re.compile(
    rf'let (?P<started>{_ID})=performance\.now\(\),(?P<response>{_ID})='
    rf'(?:await |(?P<callback>\((?P<callback_client>{_ID})\)=>))'
    rf'(?P<client>{_ID})\.beta\.messages\.create\((?P<request>{_ID}),'
    rf'(?P<options>\{{signal:(?P<signal>{_ID}),\.\.\.(?P<timeout>{_ID})!==void 0&&'
    rf'\{{timeout:(?P=timeout)\}}\}})\)'
    rf'|return (?P<retry_client>{_ID})\.beta\.messages\.create\('
    rf'(?P<retry_request>{_ID}),(?P<retry_options>\{{signal:{_ID},'
    rf'\.\.\.(?P<retry_timeout>{_ID})!==void 0&&\{{timeout:(?P=retry_timeout)\}},'
    rf'\.\.\.{_ID}!==null&&\{{maxRetries:0\}},'
    rf'(?:\.\.\.Object\.keys\((?P<retry_headers>{_ID})\)\.length>0&&'
    rf'\{{headers:(?P=retry_headers)\}}|'
    rf'\.\.\.(?P<dispatch>{_ID})!==null&&\{{headers:\{{\[{_ID}\]:(?P=dispatch)\}}\}})\}})\)(?=\.catch\()'
)
_MULTI_PROVIDER_COUNT_TOKENS = re.compile(
    rf'let (?P<client>{_ID})=await (?P<factory>{_ID})\(\{{maxRetries:1,model:(?P<model>{_ID}),'
    rf'source:"count_tokens"(?P<agent_context>,agentContext:{_ID}\(\))?'
    rf'(?P<credentials>,credentials:{_ID}(?:\?\.credentials)?)?\}}\),'
    rf'(?P<betas>{_ID})=(?P<raw_betas>{_ID})\.filter\('
    rf'\((?P<beta>{_ID})\)=>(?P<allowed_betas>{_ID})\.has\((?P=beta)\)\),(?P<response>{_ID})=await '
    rf'(?P=client)\.beta\.messages\.countTokens\((?P<request>\{{model:(?P<normalize>{_ID})\('
    rf'(?P=model)\),messages:.{{0,500}}?\}})\)'
)
_MULTI_PROVIDER_COUNT_TOKENS_CATCH = re.compile(
    rf'(?P<prefix>async function (?P<function>{_ID})\((?P<messages>{_ID}),(?P<tools>{_ID}),'
    rf'(?P<model_arg>{_ID})(?:,{_ID})?\)\{{'
    rf'(?:(?P=messages)={_ID}\((?:{_ID}\()?(?P=messages)\)\)?;let (?P<prepared_tools>{_ID})='
    rf'{_ID}\((?P=tools)\);)?return .{{0,200}}?async\(\)=>\{{try\{{.{{0,1800}}?return '
    rf'(?P<response>{_ID})\.input_tokens)\}}catch\((?P<error>{_ID})\)\{{'
    rf'(?P<body>(?:return |if\()(?P<logger>{_ID})\(`countTokens API call failed:'
    rf'.{{0,500}}?null(?:;return null)?)\}}\}}\)\}}'
)
_MULTI_PROVIDER_PICKER = re.compile(
    rf'function (?P<function>{_ID})\((?P<flag>{_ID})(?:,{_ID})?\)\{{let (?P<options>{_ID})='
    rf'(?P<native>{_ID})\((?P=flag)\),(?P<custom>{_ID})=(?:process\.env|{_ID})\.'
    r'ANTHROPIC_CUSTOM_MODEL_OPTION;'
    rf'|function (?P<served_function>{_ID})\((?P<served_flag>{_ID}),(?P<stats>{_ID})\)'
    rf'\{{let (?P<served>{_ID})={_ID}\((?P=served_flag),(?P=stats)\),'
    rf'(?P<served_options>{_ID})=(?P=served)\?\?{_ID}\((?P=served_flag)\)'
    rf'(?P<served_tail>,{_ID}=(?:process\.env|{_ID})\.ANTHROPIC_CUSTOM_MODEL_OPTION;'
    rf'|,[^;]{{1,100}};if\({_ID}\)\{{[^{{}}]{{1,300}}\}}let '
    rf'[^;]{{1,100}},\w+=(?:process\.env|{_ID})\.ANTHROPIC_CUSTOM_MODEL_OPTION;)'
)
_MULTI_PROVIDER_MODEL_KNOWLEDGE = re.compile(
    rf'(?P<prefix>isKnown:\((?P<model>{_ID})\)=>)'
    rf'(?P<native>{_ID}\({_ID}\((?P=model),\{{identity:!0\}}\)\))'
    rf'(?=,isModelId:\({_ID}\)=>)'
)


def _recognize_provider_catalog(source: str) -> str:
    matches = list(_MULTI_PROVIDER_MODEL_KNOWLEDGE.finditer(source))
    if (
        not matches
        and "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT" not in source
    ):
        return source
    if len(matches) != 1:
        raise PatchError(
            "multi-provider-sdk: model knowledge registration absent or ambiguous"
        )
    match = matches[0]
    return checked_replace(
        source,
        match.group(0),
        match.group("prefix")
        + f"_ccMultiProviderCatalogInfo({match.group('model')})!==null||"
        + match.group("native"),
        context="provider model knowledge",
    )


# The startup notice uses window classification, not modelKnowledge.isKnown.
_MULTI_PROVIDER_UNKNOWN_WINDOW = re.compile(
    rf'(?P<prefix>if\({_ID}\(\)&&!{_ID}\.'
    rf'CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT&&)'
    rf'(?P<native>!{_ID}\((?P<model>{_ID}),{_ID}\)&&!{_ID}\((?P=model)\)'
    rf'&&!{_ID}&&!{_ID}\((?P=model),{_ID}\))'
    rf'(?=\)return\{{window:{_ID},configured:{_ID},source:"unknown-model"\}})'
)


def _recognize_provider_window(source: str) -> str:
    matches = list(_MULTI_PROVIDER_UNKNOWN_WINDOW.finditer(source))
    if (
        not matches
        and "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT" not in source
    ):
        return source
    if len(matches) != 1:
        raise PatchError(
            "multi-provider-sdk: unknown model window guard absent or ambiguous"
        )
    match = matches[0]
    return checked_replace(
        source,
        match.group(0),
        match.group("prefix")
        + f"_ccMultiProviderCatalogInfo({match.group('model')})===null&&"
        + match.group("native"),
        context="provider unknown model window",
    )


# Provider endpoints do not implement Anthropic Message Threads inheritance.
_MULTI_PROVIDER_STATELESS_MODEL = re.compile(
    rf'(?P<declaration>function {_ID}\((?P<model>{_ID})\)\{{)'
    rf'(?=let {_ID}={_ID}\("tengu_curious_tower_stateless_models",""\);)'
)


def _disable_provider_message_threads(source: str) -> str:
    matches = list(_MULTI_PROVIDER_STATELESS_MODEL.finditer(source))
    if not matches and "CLAUDE_CODE_TETHER_LIVE" not in source:
        return source
    if len(matches) != 1:
        raise PatchError(
            "multi-provider-sdk: message threads model guard absent or ambiguous"
        )
    match = matches[0]
    return checked_replace(
        source,
        match.group(0),
        match.group(0)
        + f'if(_ccMultiProviderModelProvider({match.group("model")})!=="anthropic")return!0;',
        context="provider message threads",
    )


_MULTI_PROVIDER_RECOGNITION = re.compile(
    rf'function (?P<function>{_ID})\((?P<model>{_ID})\)\{{let (?P<name>{_ID})='
    rf'(?P<display>{_ID})\((?P=model)\);if\(!(?P=name)\)return null;let '
    rf'(?P<normalized>{_ID})=(?P<normalize>{_ID})\((?P=model)\),(?P<alias>{_ID})=null;'
    r'if\((?P=normalized)\.includes\("fable"\)\)'
)
_MULTI_PROVIDER_TOOL_SCHEMA = re.compile(
    rf'(?P<schemas>{_ID})=await Promise\.all\((?P<tools>{_ID})\.map\('
    rf'\((?P<tool>{_ID})\)=>(?P<serialize>{_ID})\((?P=tool),\{{'
    rf'getToolPermissionContext:(?P<context>{_ID})\.getToolPermissionContext,'
    rf'tools:(?P<all_tools>{_ID}),agents:(?P=context)\.agents,'
    rf'allowedAgentTypes:(?P=context)\.allowedAgentTypes,model:(?P<model>{_ID}),'
    rf'(?P<snapshot_fields>(?:proactivityLevel:(?P=context)\.proactivityLevel,)?'
    rf'(?:(?:querySource:(?P=context)\.querySource,)?recordedDescription:[^;{{}}]{{1,200}},'
    rf'(?:recordedEntry:[^;{{}}]{{1,200}},)?)?)'
    rf'deferLoading:(?P<deferred>{_ID})\((?P=tool)\)\}}\)\)\)(?P<delimiter>;|,)'
)
_MULTI_PROVIDER_CONTEXT_WINDOW = re.compile(
    rf'(?P<prefix>function (?P<function>{_ID})\((?P<model>{_ID}),(?P<headers>{_ID})\)'
    rf'\{{let (?P<override>{_ID})=(?P<override_fn>{_ID})\(\);'
    rf'if\((?P=override)!==void 0\)return (?P=override);)'
    rf'(?P<native>if\((?P<extended>{_ID})\((?P=model),(?P=headers)\)\)'
    rf'return (?P<extended_window>{_ID});return (?P<nested>{_ID})'
    rf'\((?P=model),(?P=headers)\)\}})'
)
_MULTI_PROVIDER_MAX_OUTPUT = re.compile(
    rf'(?P<prefix>function (?P<function>{_ID})\((?P<model>{_ID})\)\{{let '
    rf'(?P<default>{_ID}),(?P<upper>{_ID}),(?P<normalized>{_ID})='
    rf'(?P<normalize>{_ID})\((?P=model)\)'
    rf'(?:,{_ID}={_ID}\((?P=model),(?P=normalized)\))?'
    rf'(?:,(?P<limits>{_ID})={_ID}\((?P=normalized)\)\?\.max_output_tokens)?;'
    rf'.{{0,2000}}?let (?P<config>{_ID})='
    rf'(?P<config_fn>{_ID})\((?P=model)\);if\((?P=config)\?\.max_tokens&&'
    rf'(?P=config)\.max_tokens>=4096\)(?P=upper)=(?P=config)\.max_tokens,'
    rf'(?P=default)=Math\.min\((?P=default),(?P=upper)\);)'
    rf'(?P<return>return\{{default:(?P=default),upperLimit:(?P=upper)\}}\}})'
)
_MULTI_PROVIDER_ATTRIBUTION = re.compile(
    rf"let (?P<model>{_ID})=(?P<current>{_ID})\(\),(?P<label>{_ID})="
    rf'(?P<native_label>[^;]{{1,300}}),(?P<pr>{_ID})='
    rf'(?P<native_pr>`\\uD83E\\uDD16 Generated with '
    rf'\[Claude Code\]\(\$\{{{_ID}\}}\)`|{_ID}\(\)),(?P<commit>{_ID})=`Co-Authored-By: '
    rf'\$\{{(?P=label)\}} <noreply@anthropic\.com>`,(?P<settings>{_ID})=(?P<load>{_ID})\(\)'
    rf'(?P<delimiter>;|,(?={_ID}=(?P=settings)\.attribution;))'
)


_MULTI_PROVIDER_COMPACTION_SOURCE = re.compile(
    rf'function (?P<function>{_ID})\((?P<model>{_ID}),(?P<setting>{_ID})\)\{{(?:let\{{'
    rf'source:(?P<source>{_ID})\}}=(?P<resolver>{_ID})\((?P=model),(?P=setting)\);'
    rf'return (?P=source)==="env"\|\|(?P=source)==="settings"\|\|'
    rf'(?:(?P=source)==="clientdata"\|\|)?(?P=source)==="model-default"'
    rf'|return (?P<direct_resolver>{_ID})\((?P=model),(?P=setting)\)\.source!=="auto")\}}'
)


def _replace_multi_provider_sdk_tail(match: re.Match[str]) -> str:
    return (
        match.group("prefix")
        + f"const _ccMultiProviderSDK=()=>{match.group('constructor')};"
        + _MULTI_PROVIDER_HELPER
        + match.group("next")
    )


def _replace_multi_provider_thinking_filter(match: re.Match[str]) -> str:
    if match.group("predicate"):
        origin, target = match.group("origin", "target")
        return match.group(0).replace(
            match.group("native"),
            f"{origin}!=={match.group('sentinel')}&&"
            f"_ccMultiProviderModelProvider({origin})!=="
            f"_ccMultiProviderModelProvider({target})",
            1,
        )
    messages = match.group("messages")
    model = match.group("model")
    message = match.group("message")
    return (
        f"function {match.group('function')}({messages},{model}){{let _ccProvider="
        f"_ccMultiProviderModelProvider({model});return {match.group('filter')}("
        f"{messages},({message})=>{message}.message.model!=="
        f"{match.group('synthetic')}&&_ccMultiProviderModelProvider("
        f"{message}.message.model)!==_ccProvider)}}"
    )


def _route_multi_provider_request(match: re.Match[str]) -> str:
    client = match.group("client")
    request = match.group("request")
    options = match.group("options")
    return (
        f"let _ccRequest={request},_ccOptions={options},"
        f"[_ccClient,_ccOutbound,_ccOutboundOptions]=_ccMultiProviderRoute({client},"
        f"_ccRequest,_ccOptions);let {match.group('response')}=await "
        "_ccClient.beta.messages.create(_ccOutbound,_ccOutboundOptions"
    )


def _route_multi_provider_side_query(match: re.Match[str]) -> str:
    if match.group("retry_client"):
        return (
            'return (([_ccClient,_ccOutbound,_ccOutboundOptions])=>'
            '_ccClient.beta.messages.create(_ccOutbound,_ccOutboundOptions))'
            f'(_ccMultiProviderRoute({match.group("retry_client")},'
            f'{match.group("retry_request")},{match.group("retry_options")}))'
        )
    options = match.group("options")
    if match.group("callback"):
        # Route each attempt with its current client. Keep the native retry handler.
        return (
            f"let {match.group('started')}=performance.now(),"
            f"{match.group('response')}={match.group('callback')}"
            "(([_ccClient,_ccOutbound,_ccOutboundOptions])=>"
            "_ccClient.beta.messages.create(_ccOutbound,_ccOutboundOptions))"
            f"(_ccMultiProviderRoute({match.group('client')},"
            f"{match.group('request')},{options}))"
        )
    return (
        f"let {match.group('started')}=performance.now(),_ccRequest="
        f"{match.group('request')},_ccOptions={options},"
        f"[_ccClient,_ccOutbound,_ccOutboundOptions]=_ccMultiProviderRoute("
        f"{match.group('client')},_ccRequest,_ccOptions),{match.group('response')}="
        "await _ccClient.beta.messages.create(_ccOutbound,_ccOutboundOptions)"
    )


def _route_multi_provider_count_tokens(match: re.Match[str]) -> str:
    request = match.group("request").replace(
        f"{match.group('normalize')}({match.group('model')})",
        f"{match.group('normalize')}(_ccEffectiveModel)",
        1,
    )
    return (
        "_ccMultiProviderPreflight(_ccEffectiveModel);let "
        f"{match.group('client')}=await {match.group('factory')}({{maxRetries:1,model:"
        '_ccEffectiveModel,source:"count_tokens"'
        f"{match.group('agent_context') or ''}{match.group('credentials') or ''}}}),"
        f"{match.group('betas')}={match.group('raw_betas')}.filter("
        f"({match.group('beta')})=>{match.group('allowed_betas')}.has("
        f"{match.group('beta')})),_ccRequest="
        f"{request},"
        "[_ccClient,_ccOutbound]="
        f"_ccMultiProviderRoute({match.group('client')},_ccRequest,{{}},!0),"
        f"{match.group('response')}=await _ccClient.beta.messages.countTokens("
        "_ccOutbound)"
    )


def _surface_multi_provider_count_tokens_error(match: re.Match[str]) -> str:
    error = match.group("error")
    prefix = match.group("prefix")
    model_arg = match.group("model_arg")
    model_pattern = re.compile(
        rf"let (?P<effective>{_ID})={re.escape(model_arg)}\?\?(?P<default>{_ID})\(\)"
    )
    bindings = discover_identifiers(prefix, (model_pattern,))
    callback_try = "async()=>{try{"
    if callback_try not in prefix:
        raise PatchError("multi-provider-sdk: countTokens try scope changed")
    prefix = prefix.replace(callback_try, "async()=>{let _ccEffectiveModel;try{", 1)
    # Keep the native local binding. Copy its value into the catch scope.
    prefix = model_pattern.sub(
        lambda _: (
            f"let {bindings['effective']}=_ccEffectiveModel="
            f"{model_arg}??{bindings['default']}()"
        ),
        prefix,
        count=1,
    )
    response = match.group("response")
    return_suffix = f"return {response}.input_tokens"
    if not prefix.endswith(return_suffix):
        raise PatchError("multi-provider-sdk: countTokens return shape changed")
    prefix = prefix[: -len(return_suffix)]
    # Validate external responses before the native malformed-response fallback.
    native_guard = f'if(typeof {response}.input_tokens!=="number")return null;'
    prefix = prefix.replace(
        native_guard,
        f"_ccMultiProviderInputTokens(_ccEffectiveModel,{response});" + native_guard,
        1,
    )
    return (
        prefix
        + f"return _ccMultiProviderInputTokens(_ccEffectiveModel,{response})}}"
        + f'catch({error}){{if(_ccMultiProviderModelInfo(_ccEffectiveModel))throw {error};'
        + match.group("body")
        + "}})}"
    )


def _add_multi_provider_picker_models(match: re.Match[str]) -> str:
    if match.group("served_options"):
        return (
            match.group(0)
            + f"if({match.group('served')}===null)"
            + f"{match.group('served_options')}.push(..._ccMultiProviderPickerCatalog());"
        )
    return (
        match.group(0)
        + f"{match.group('options')}.push(..._ccMultiProviderPickerCatalog());"
    )


def _recognize_multi_provider_model(match: re.Match[str]) -> str:
    model = match.group("model")
    name = match.group("name")
    return (
        f"function {match.group('function')}({model}){{let _ccKnown="
        f"_ccMultiProviderCatalog.find((_ccModel)=>_ccModel.value==={model});"
        f"if(_ccKnown)return{{..._ccKnown}};let {name}={match.group('display')}({model});"
        f"if(!{name})return null;let {match.group('normalized')}="
        f"{match.group('normalize')}({model}),{match.group('alias')}=null;"
        f'if({match.group("normalized")}.includes("fable"))'
    )


def _filter_multi_provider_tool_schemas(match: re.Match[str]) -> str:
    tools = match.group("tools")
    tool = match.group("tool")
    if match.group("snapshot_fields"):
        # Keep schema indices aligned with the native tool and snapshot maps.
        return match.group(0).replace(
            f"{tools}.map(({tool})=>",
            f"({tools}={tools}.filter(({tool})=>_ccMultiProviderToolAllowed("
            f"{match.group('model')},{tool}))).map(({tool})=>",
            1,
        )
    return match.group(0).replace(
        f"{tools}.map(({tool})=>",
        f"{tools}.filter(({tool})=>_ccMultiProviderToolAllowed("
        f"{match.group('model')},{tool})).map(({tool})=>",
        1,
    )


def _resolve_multi_provider_context_window(match: re.Match[str]) -> str:
    model = match.group("model")
    return (
        match.group("prefix")
        + f"let _ccProviderModel=_ccMultiProviderCatalogInfo({model});"
        + "if(_ccProviderModel)return _ccProviderModel.contextWindow;"
        + match.group("native")
    )


def _resolve_multi_provider_max_output(match: re.Match[str]) -> str:
    model = match.group("model")
    default = match.group("default")
    upper = match.group("upper")
    return (
        match.group("prefix")
        + f"let _ccProviderModel=_ccMultiProviderCatalogInfo({model});"
        + f"if(_ccProviderModel){upper}=_ccProviderModel.maxOutputTokens,"
        + f"{default}=Math.min({default},{upper});"
        + match.group("return")
    )


def _replace_multi_provider_attribution(match: re.Match[str]) -> str:
    model = match.group("model")
    label = match.group("label")
    pr = match.group("pr")
    commit = match.group("commit")
    settings = match.group("settings")
    return (
        f"let {model}=_ccAttributionModel??{match.group('current')}(),"
        f"_ccNativeAttributionLabel={match.group('native_label')},"
        f"{{label:{label},domain:_ccAttributionDomain}}="
        f"_ccMultiProviderAttribution({model},_ccNativeAttributionLabel),"
        f"{pr}={match.group('native_pr')},"
        f"{commit}=`Co-Authored-By: ${{{label}}} <noreply@${{_ccAttributionDomain}}>`,"
        f"{settings}={match.group('load')}(){match.group('delimiter')}"
    )


def _attribution_match(pattern: str, source: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise PatchError(
            f"multi-provider attribution: expected one match, got {len(matches)}: "
            f"{pattern!r}"
        )
    return matches[0]


def _attribution_function(source: str, name: str, arguments: str = "[^)]*") -> str:
    return _attribution_match(
        rf"\bfunction {re.escape(name)}\({arguments}\)\{{[\s\S]*?\}}"
        rf"(?=\s*(?:(?:async )?function\b|class\b|var\b|let\b|const\b)|\s*$)",
        source,
    ).group(0)


def _attribution_replace(source: str, old: str, new: str) -> str:
    return checked_replace(source, old, new, context="multi-provider attribution")


@dataclass(frozen=True)
class _AttributionDiscovery:
    attribution: re.Match[str]
    base_name: str
    base_header: str
    effective: str
    wrapper: str | None
    sections: tuple[re.Match[str], ...]
    prompt: re.Match[str]
    dispatch_body: str
    edge: re.Match[str]
    compact_body: str
    serializer: re.Match[str]


def _discover_multi_provider_attribution(source: str) -> _AttributionDiscovery:
    attribution = _attribution_match(_MULTI_PROVIDER_ATTRIBUTION.pattern, source)
    base = _attribution_match(
        rf"function (?P<base>{_ID})\(\)\{{"
        rf"(?:(?!\bfunction\b)[\s\S]){{0,2000}}?{re.escape(attribution.group(0))}",
        source,
    )
    base_name = base.group("base")
    base_header = f"function {base_name}()"
    wrapper: str | None = None

    # Discover both git sections by their shared native attribution call.
    sections = list(
        re.finditer(
            rf'function (?P<section>{_ID})\((?P<arg>{_ID})\)\{{(?:let [^;]{{1,150}};)?if\(!{_ID}\(\)\)'
            rf'return"";let [^;]{{0,150}}?\{{(?:commit:{_ID},pr:{_ID}|pr:{_ID},commit:{_ID})\}}=(?P<effective>{_ID})\(\)',
            source,
        )
    )
    if len(sections) != 2 or len({m.group("effective") for m in sections}) != 1:
        raise PatchError("multi-provider attribution: git sections changed")
    effective = sections[0].group("effective")
    if effective != base_name:
        wrapper = _attribution_function(source, effective, "")
        binding_orders = (
            rf'(?P<link>{_ID})={_ID}\(\),(?P<value>{_ID})={re.escape(base_name)}\(\)',
            rf'(?P<value>{_ID})={re.escape(base_name)}\(\),(?P<link>{_ID})={_ID}\(\)',
        )
        returns = (
            rf'return (?P=link)\?{_ID}\((?P=value),(?P=link)\):(?P=value)',
            rf'if\(!(?P=link)\)return (?P=value);return '
            rf'{_ID}\((?P=value),(?P=link)\.url,{_ID}\((?P=link)\)\)',
        )
        matches = [
            re.fullmatch(
                rf'function {re.escape(effective)}\(\)\{{'
                rf'(?:if\({_ID}\(\)==="remote"&&{_ID}\.CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION\)'
                rf'return\{{commit:"",pr:""\}};)?let {bindings};{result}\}}',
                wrapper,
            )
            for bindings in binding_orders
            for result in returns
        ]
        if sum(result is not None for result in matches) != 1:
            raise PatchError("multi-provider attribution: session-link wrapper changed")

    prompt = _attribution_match(
        rf'async prompt\(\{{model:(?P<model>{_ID}),tools:(?P<tools>{_ID})\}}\)'
        rf'\{{(?P<body>[\s\S]{{0,500}}?)return (?P<dispatch>{_ID})\((?P=model),'
        rf'(?P<flags>[\s\S]{{0,200}}?)\)\}},isConcurrencySafe',
        source,
    )
    dispatch = prompt.group("dispatch")
    dispatch_body = _attribution_function(source, dispatch)
    edge = _attribution_match(
        rf'function {re.escape(dispatch)}\((?P<model>{_ID}),(?P<flags>{_ID})\)'
        rf'\{{if\({_ID}\((?P=model)\)\)return (?P<compact>{_ID})\((?P=flags)\);',
        dispatch_body,
    )
    compact = edge.group("compact")
    compact_body = _attribution_function(source, compact)
    serializer = _attribution_match(
        rf'async function (?P<serialize>{_ID})\((?P<tool>{_ID}),(?P<context>{_ID})\)'
        rf'\{{(?P<prefix>[^{{}}]{{0,1800}}?)(?P<key>{_ID})='
        rf'(?P<key_expr>(?:{_ID}\+|""\+){{2,20}}\("inputJSONSchema"in (?P=tool)&&'
        rf'(?P=tool)\.inputJSONSchema\?`\$\{{(?P=tool)\.name\}}:\$\{{{_ID}(?:\.schemaKey)?'
        rf'\((?P=tool)\.inputJSONSchema\)\}}`:(?P=tool)\.name\)),'
        rf'(?:(?P<cache>{_ID})={_ID}\(\),)?(?P<value>{_ID})='
        rf'(?(cache)(?P=cache)|{_ID})\.get\((?P=key)\);',
        source,
    )
    return _AttributionDiscovery(
        attribution,
        base_name,
        base_header,
        effective,
        wrapper,
        tuple(sections),
        prompt,
        dispatch_body,
        edge,
        compact_body,
        serializer,
    )


def _transform_attribution_rules(source: str, found: _AttributionDiscovery) -> str:
    source = _attribution_replace(
        source, found.base_header, f"function {found.base_name}(_ccAttributionModel)"
    )
    source = _attribution_replace(
        source,
        found.attribution.group(0),
        _replace_multi_provider_attribution(found.attribution),
    )
    if found.wrapper is not None:
        wrapper = _attribution_replace(
            found.wrapper,
            f"function {found.effective}()",
            f"function {found.effective}(_ccAttributionModel)",
        )
        wrapper = _attribution_replace(
            wrapper, f"{found.base_name}()", f"{found.base_name}(_ccAttributionModel)"
        )
        source = _attribution_replace(source, found.wrapper, wrapper)
    return source


def _transform_attribution_prompts(source: str, found: _AttributionDiscovery) -> str:
    prompt = found.prompt
    dispatch = prompt.group("dispatch")
    dispatch_body = found.dispatch_body
    compact_body = found.compact_body
    compact, flags, model = found.edge.group("compact", "flags", "model")
    effective = found.effective
    updated_dispatch = _attribution_replace(
        dispatch_body,
        f"function {dispatch}({model},{flags})",
        f"function {dispatch}({model},{flags},_ccAttributionSnapshot)",
    )
    updated_dispatch = _attribution_replace(
        updated_dispatch,
        f"{compact}({flags})",
        f"{compact}({flags},{model},_ccAttributionSnapshot)",
    )
    compact_arg = _attribution_match(
        rf'function {re.escape(compact)}\((?P<arg>{_ID})\)', compact_body
    ).group("arg")
    updated_compact = _attribution_replace(
        compact_body,
        f"function {compact}({compact_arg})",
        f"function {compact}({compact_arg},_ccAttributionModel,_ccAttributionSnapshot)",
    )
    for section in found.sections:
        name, arg = section.group("section", "arg")
        body = _attribution_function(source, name)
        updated = _attribution_replace(
            body,
            f"function {name}({arg})",
            f"function {name}({arg},_ccAttributionModel,_ccAttributionSnapshot)",
        )
        updated = _attribution_replace(
            updated,
            f"{effective}()",
            f"(_ccAttributionSnapshot??{effective}(_ccAttributionModel))",
        )
        source = _attribution_replace(source, body, updated)
        in_dispatch = f"{name}({flags})" in dispatch_body
        in_compact = f"{name}({compact_arg})" in compact_body
        if in_dispatch == in_compact:
            raise PatchError("multi-provider attribution: ambiguous git section caller")
        if in_dispatch:
            updated_dispatch = _attribution_replace(
                updated_dispatch,
                f"{name}({flags})",
                f"{name}({flags},{model},_ccAttributionSnapshot)",
            )
        else:
            updated_compact = _attribution_replace(
                updated_compact,
                f"{name}({compact_arg})",
                f"{name}({compact_arg},_ccAttributionModel,_ccAttributionSnapshot)",
            )
    source = _attribution_replace(source, dispatch_body, updated_dispatch)
    source = _attribution_replace(source, compact_body, updated_compact)
    for section in found.sections:
        name = re.escape(section.group("section"))
        if re.search(rf"(?<![\w$]){name}\({_ID}\)", source):
            raise PatchError(
                "multi-provider attribution: unthreaded git section caller"
            )
    source = _attribution_replace(
        source,
        prompt.group(0),
        f'async prompt({{model:{prompt.group("model")},tools:{prompt.group("tools")},'
        '_ccAttributionSnapshot}){_ccAttributionSnapshot??=Object.freeze('
        f'{effective}({prompt.group("model")}));{prompt.group("body")}return '
        f'{dispatch}({prompt.group("model")},{prompt.group("flags")},'
        '_ccAttributionSnapshot)},isConcurrencySafe',
    )

    return source


def _transform_attribution_serializer(source: str, found: _AttributionDiscovery) -> str:
    # Snapshot before the first await. The cache key and prompt use the same values.
    serializer = found.serializer
    effective = found.effective
    context, tool = serializer.group("context", "tool")
    original = serializer.group(0)
    source = _attribution_replace(
        source,
        original,
        _attribution_replace(
            original,
            serializer.group("key_expr"),
            serializer.group("key_expr")
            + f'+JSON.stringify([{context}.model??null,{context}._ccAttributionSnapshot??null])',
        ),
    )
    # Carry the snapshot on each returned schema, including cache hits and stripped schemas.
    # An enumerable symbol survives object spreads but does not enter the JSON payload.
    serialize = serializer.group("serialize")
    declaration = f"async function {serialize}({tool},{context})"
    source = _attribution_replace(
        source,
        declaration,
        f"async function {serialize}({tool},{context}){{"
        f'{context}={{...{context},_ccAttributionSnapshot:{tool}.name==="Bash"?'
        f'Object.freeze({effective}({context}.model)):void 0}};'
        f"let _ccSchema=await {serialize}_ccInner({tool},{context});"
        f"if({context}._ccAttributionSnapshot)Object.defineProperty(_ccSchema,"
        f"_ccAttributionKey,{{value:{context}._ccAttributionSnapshot,enumerable:!0}});"
        f"return _ccSchema}}async function {serialize}_ccInner({tool},{context})",
    )
    return source


def _transform_attribution_route(source: str, effective: str) -> str:
    # Only the actual Z.ai create request gets the additional system instruction.
    source = _attribution_replace(
        source,
        "function _ccMultiProviderRoute(_ccNativeClient,_ccRequest,_ccOptions={})",
        "function _ccMultiProviderRoute(_ccNativeClient,_ccRequest,_ccOptions={},_ccCountOnly=!1)",
    )
    source = _attribution_replace(
        source,
        "delete _ccOutbound[_ccField];return[_ccCached.client,_ccOutbound,",
        'delete _ccOutbound[_ccField];if(_ccInfo.provider==="zai"&&!_ccCountOnly)'
        '_ccOutbound.system=_ccMultiProviderSystemAttribution(_ccRequest.system,_ccRequest.model,'
        '_ccRequest.tools?.find((_ccTool)=>_ccTool.name==="Bash")?.[_ccAttributionKey]);'
        'return[_ccCached.client,_ccOutbound,',
    )
    system_helper = (
        'const _ccAttributionKey=Symbol("ccpatch.attribution");'
        'function _ccMultiProviderSystemAttribution(_ccSystem,_ccModel,_ccSnapshot){'
        'let _ccMarker="<ccpatch-git-attribution>",_ccEnd="</ccpatch-git-attribution>",'
        '_ccBlocks=typeof _ccSystem==="string"?[{type:"text",text:_ccSystem}]:'
        '[...(_ccSystem??[])];_ccBlocks=_ccBlocks.filter((_ccBlock)=>!('
        '_ccBlock.type==="text"&&_ccBlock.text.startsWith(_ccMarker)&&'
        '_ccBlock.text.endsWith(_ccEnd)));'
        f'let _ccAttribution=_ccSnapshot??Object.freeze({effective}(_ccModel)),_ccLines=[];'
        'if(_ccAttribution.commit)_ccLines.push("End git commit messages with:\\n"+'
        '_ccAttribution.commit);if(_ccAttribution.pr)_ccLines.push("End PR bodies with:\\n"+'
        '_ccAttribution.pr);if(_ccLines.length)_ccBlocks.push({type:"text",text:'
        '_ccMarker+"\\n"+_ccLines.join("\\n")+"\\n"+_ccEnd});return _ccBlocks}'
    )
    source = _attribution_replace(
        source,
        "function _ccMultiProviderRoute(",
        system_helper + "function _ccMultiProviderRoute(",
    )
    return source


def _thread_modern_attribution(source: str) -> str:
    """Keep native async policy and replay branches in request-local scope."""
    module = next(
        item
        for item in source_modules(source)
        if "# Committing changes with git" in item.source
    )
    text = module.source
    position = text.index("- Interactive flags (")
    start = list(re.finditer(rf"(?:async )?function {_ID}\(", text[:position]))[
        -1
    ].start()
    effective = _attribution_match(
        rf"(?:\{{(?:commit:{_ID},pr:{_ID}|pr:{_ID},commit:{_ID})\}}=|let {_ID}=)(?:await )?(?P<name>{_ID})\(\)",
        text[start:position],
    ).group("name")
    modern = re.search(
        rf"function (?P<base>{_ID})\((?P<arg>{_ID})?\)\{{let (?P<pr>{_ID})=(?P<footer>{_ID})\(\),"
        rf"(?P<commit>{_ID})=`Co-Authored-By: \$\{{(?P<label>{_ID})\((?P<model>{_ID}(?:\(\))?)\)\}} <noreply@anthropic\.com>`",
        text,
    )
    if modern is not None:
        old = modern.group(0)
        # Native explicit models also cover recorded attribution snapshots.
        model = (
            modern.group("arg")
            or f"_ccAttributionScope.getStore()?.model??{modern.group('model')}"
        )
        if modern.group("arg"):
            native_call = _attribution_match(
                rf"{re.escape(modern.group('base'))}\((?P<model>{_ID})\?\?(?P<current>{_ID})\(\)\)",
                text,
            )
            text = _attribution_replace(
                text,
                native_call.group(0),
                f"{modern.group('base')}({native_call.group('model')}??_ccAttributionScope.getStore()?.model??{native_call.group('current')}())",
            )
        replacement = (
            old[: old.index("let ")] + f"let _ccModel={model},"
            f"_ccNativeAttributionLabel={modern.group('label')}(_ccModel),"
            "{label:_ccLabel,domain:_ccAttributionDomain}="
            "_ccMultiProviderAttribution(_ccModel,_ccNativeAttributionLabel),"
            f"{modern.group('pr')}={modern.group('footer')}(),"
            f"{modern.group('commit')}=`Co-Authored-By: ${{_ccLabel}} <noreply@${{_ccAttributionDomain}}>`"
        )
        text = _attribution_replace(text, old, replacement)
    else:
        attribution = _attribution_match(_MULTI_PROVIDER_ATTRIBUTION.pattern, text)
        replacement = _replace_multi_provider_attribution(attribution).replace(
            "_ccAttributionModel??", "_ccAttributionScope.getStore()?.model??"
        )
        text = _attribution_replace(text, attribution.group(0), replacement)
    prompt = _attribution_match(
        rf"async prompt\(\{{model:(?P<model>{_ID}),tools:(?P<tools>{_ID})(?P<extra>[^}}]*)\}}\)"
        rf"\{{(?P<body>[\s\S]{{0,600}}?)return (?P<dispatch>{_ID})\((?P<args>[^;{{}}]*?)\)\}},isConcurrencySafe",
        text,
    )
    replacement = (
        f"async prompt({{model:{prompt.group('model')},tools:{prompt.group('tools')}{prompt.group('extra')},_ccAttributionSnapshot}}){{"
        f"return _ccAttributionScope.run({{model:{prompt.group('model')},snapshot:_ccAttributionSnapshot}},async()=>{{"
        f"if(_ccAttributionSnapshot===void 0)_ccAttributionScope.getStore().snapshot=await {effective}();"
        + prompt.group("body")
        + f"return {prompt.group('dispatch')}({prompt.group('args')})}})}},isConcurrencySafe"
    )
    for anchor in ("- Interactive flags (", "# Committing changes with git"):
        position = text.index(anchor)
        start = list(re.finditer(rf"(?:async )?function {_ID}\(", text[:position]))[
            -1
        ].start()
        prefix = text[start:position]
        updated = _attribution_replace(
            prefix,
            f"{effective}()",
            f"(_ccAttributionScope.getStore()?.snapshot!==void 0?_ccAttributionScope.getStore().snapshot:{effective}())",
        )
        text = _attribution_replace(text, prefix, updated)
    text = _attribution_replace(text, prompt.group(0), replacement)
    serializer = _attribution_match(
        rf"async function (?P<name>{_ID})\((?P<tool>{_ID}),(?P<context>{_ID})\)\{{(?:(?!\bfunction\b)[\s\S]){{0,5000}}?"
        rf"(?P<key>(?:{_ID}\+|\"\"\+){{2,30}}\(\"inputJSONSchema\"in (?P=tool)&&[^;]+?:(?P=tool)\.name\))",
        text,
    )
    tool, context, serialize = serializer.group("tool", "context", "name")
    text = _attribution_replace(
        text,
        serializer.group("key"),
        serializer.group("key")
        + f"+JSON.stringify([{context}.model??null,{context}._ccAttributionSnapshot??null])",
    )
    declaration = f"async function {serialize}({tool},{context})"
    text = _attribution_replace(
        text,
        declaration,
        declaration
        + "{"
        + f"return _ccAttributionScope.run({{model:{context}.model}},async()=>{{"
        + f"{context}={{...{context},_ccAttributionSnapshot:{tool}.name===\"Bash\"?await {effective}():void 0}};"
        + f"if({context}._ccAttributionSnapshot)Object.freeze({context}._ccAttributionSnapshot);"
        + f"let _ccSchema=await {serialize}_ccInner({tool},{context});"
        + f"if({tool}.name===\"Bash\")Object.defineProperty(_ccSchema,Symbol.for(\"ccpatch.attribution\"),{{value:{context}._ccAttributionSnapshot,enumerable:!0}});return _ccSchema}})}}"
        + f"async function {serialize}_ccInner({tool},{context})",
    )
    text = (
        'var _ccAttributionScope=new (import.meta.require("node:async_hooks").AsyncLocalStorage);'
        + text
    )
    source = _attribution_replace(source, module.source, text)
    source = _transform_attribution_route(source, effective)
    source = _attribution_replace(
        source,
        'const _ccAttributionKey=Symbol("ccpatch.attribution");',
        'const _ccAttributionKey=Symbol.for("ccpatch.attribution");',
    )
    # Missing snapshots must not bypass native policy or replay handling.
    return _attribution_replace(
        source,
        f"let _ccAttribution=_ccSnapshot??Object.freeze({effective}(_ccModel)),_ccLines=[];",
        "if(!_ccSnapshot)return _ccBlocks;let _ccAttribution=_ccSnapshot,_ccLines=[];",
    )


def _thread_multi_provider_attribution(match: re.Match[str]) -> str:
    """Keep native attribution rules and pass request-local values through Bash."""
    source = match.group(0)
    if (
        len(source_modules(source)) > 1
        or re.search(
            rf"function {_ID}\((?P<model>{_ID}),(?P<flags>{_ID})\)\{{if\({_ID}\((?P=model)\)\)return {_ID}\((?P=flags),(?P=model)\);",
            source,
        )
        or re.search(
            r"async prompt\(\{model:[\w$]+,tools:[\w$]+,(?:proactiveLevelActive|leanPrompt):",
            source,
        )
    ):
        return _thread_modern_attribution(source)
    found = _discover_multi_provider_attribution(source)
    source = _transform_attribution_rules(source, found)
    source = _transform_attribution_prompts(source, found)
    source = _transform_attribution_serializer(source, found)
    return _transform_attribution_route(source, found.effective)


def _mark_multi_provider_compaction_source(match: re.Match[str]) -> str:
    source = match.group("source")
    model = match.group("model")
    if match.group("direct_resolver") is not None:
        return (
            f"function {match.group('function')}({model},{match.group('setting')}){{"
            f"let _ccSource={match.group('direct_resolver')}"
            f"({model},{match.group('setting')}).source;"
            'return _ccSource!=="auto"||(_ccSource==="auto"&&'
            f"_ccMultiProviderCatalogInfo({model})!==null)}}"
        )
    return (
        match.group(0)[:-1]
        + f'||({source}==="auto"&&_ccMultiProviderCatalogInfo({model})!==null)'
        + "}"
    )


class _SDKPatchSet(PatchSet):
    @typing_override
    def apply(self, source: str) -> str:
        split = len(source_modules(source)) > 1
        if split:
            for pattern in _MULTI_PROVIDER_AGENT_IDENTIFIERS:
                definition = pattern.search(source)
                consumer = _MULTI_PROVIDER_AGENT_MODEL.search(source)
                if definition is None or consumer is None:
                    raise PatchError("multi-provider-sdk: missing agent binding anchor")
                for name, value in definition.groupdict().items():
                    if value is not None and name != "entry":
                        source, _ = ensure_module_reference(
                            source, definition.start(), value, consumer.start()
                        )
                        definition = pattern.search(source)
                        consumer = _MULTI_PROVIDER_AGENT_MODEL.search(source)
                        assert definition is not None and consumer is not None
        native_tail = _MULTI_PROVIDER_SDK_TAIL.search(source) if split else None
        source = super().apply(source)
        source = _recognize_provider_catalog(source)
        source = _recognize_provider_window(source)
        source = _disable_provider_message_threads(source)
        if not split:
            return source
        assert native_tail is not None
        constructor = native_tail.group("constructor")
        factory_start = native_tail.string.rfind(
            "async function ", 0, native_tail.start()
        )
        factory = re.match(
            rf"async function {_ID}\(\{{[^{{}}]+\}}\)\{{",
            native_tail.string[factory_start:],
        )
        if factory is None:
            raise PatchError("multi-provider-sdk: missing native SDK factory entry")
        source = checked_replace(
            source,
            factory[0],
            factory[0] + f"globalThis.__ccpatchRuntime.sdk.SDK=()=>{constructor};",
            context="SDK native constructor capture",
        )
        capture = re.compile(r"const _ccMultiProviderSDK=\(\)=>[\w$]+;")
        source, count = capture.subn("", source)
        if count != 1:
            raise PatchError("multi-provider-sdk: ambiguous SDK capture")
        helper_start = source.find("const _ccMultiProviderDefinitions=")
        helper_end = source.find(native_tail.group("next"), helper_start)
        if helper_start < 0 or helper_end < 0:
            raise PatchError("multi-provider-sdk: missing SDK helper boundaries")
        helper = source[helper_start:helper_end]
        source = checked_replace(source, helper, "", context="SDK singleton extraction")
        names = sorted(set(re.findall(r"\b_ccMultiProvider\w+", helper)))
        names.remove("_ccMultiProviderSDK")
        helpers = re.compile(r"\b(" + "|".join(names) + r")\b")
        source = helpers.sub(r"globalThis.__ccpatchRuntime.sdk.\1", source)
        code = helper.replace(
            "_ccMultiProviderSDK()", "globalThis.__ccpatchRuntime.sdk.SDK()"
        )
        code += "return {" + ",".join(names) + "};"
        return register_module_bootstrap(source, "sdk", code)


# The router propagates provider compatibility errors. It does not fall back to
# Anthropic or change the native client's retry policy.
MULTI_PROVIDER_SDK = _SDKPatchSet(
    name="multi-provider-sdk",
    patches=(
        _model_costs_patch(_MULTI_PROVIDER_MODEL_COSTS),
        Patch(
            "capture-generic-anthropic-sdk",
            _MULTI_PROVIDER_SDK_TAIL,
            _replace_multi_provider_sdk_tail,
        ),
        Patch(
            "resolve-provider-context-window",
            _MULTI_PROVIDER_CONTEXT_WINDOW,
            _resolve_multi_provider_context_window,
        ),
        Patch(
            "resolve-provider-max-output",
            _MULTI_PROVIDER_MAX_OUTPUT,
            _resolve_multi_provider_max_output,
        ),
        Patch(
            "select-provider-attribution",
            re.compile(r"\A[\s\S]+\Z"),
            _thread_multi_provider_attribution,
        ),
        Patch(
            "mark-provider-compaction-source",
            _MULTI_PROVIDER_COMPACTION_SOURCE,
            _mark_multi_provider_compaction_source,
        ),
        Patch(
            "retain-same-provider-thinking",
            _MULTI_PROVIDER_THINKING_FILTER,
            _replace_multi_provider_thinking_filter,
        ),
        Patch(
            "route-nonstreaming-fallback",
            _MULTI_PROVIDER_NONSTREAMING,
            _route_multi_provider_request,
        ),
        Patch(
            "route-main-streaming",
            _MULTI_PROVIDER_STREAMING,
            _route_multi_provider_request,
        ),
        Patch(
            "route-side-query",
            _MULTI_PROVIDER_SIDE_QUERY,
            _route_multi_provider_side_query,
        ),
        Patch(
            "route-count-tokens",
            _MULTI_PROVIDER_COUNT_TOKENS,
            _route_multi_provider_count_tokens,
        ),
        Patch(
            "surface-count-tokens-provider-errors",
            _MULTI_PROVIDER_COUNT_TOKENS_CATCH,
            _surface_multi_provider_count_tokens_error,
        ),
        Patch(
            "add-model-picker-entries",
            _MULTI_PROVIDER_PICKER,
            _add_multi_provider_picker_models,
        ),
        Patch(
            "restore-provider-qualified-session-model",
            _MULTI_PROVIDER_RESUME,
            _restore_multi_provider_model,
        ),
        Patch(
            "expand-agent-model-catalogue",
            _MULTI_PROVIDER_AGENT_MODEL,
            "",
            identifiers=_MULTI_PROVIDER_AGENT_IDENTIFIERS,
            bound_replacement=_expand_multi_provider_agent_model,
        ),
        Patch(
            "recognize-qualified-models",
            _MULTI_PROVIDER_RECOGNITION,
            _recognize_multi_provider_model,
        ),
        Patch(
            "allow-web-search-only-for-anthropic-models",
            _MULTI_PROVIDER_TOOL_SCHEMA,
            _filter_multi_provider_tool_schemas,
        ),
    ),
    verify_present=(
        re.compile(r"const _ccMultiProviderSDK=\(\)=>[\w$]+"),
        re.compile(r"function _ccMultiProviderRoute\("),
        re.compile(r'maxRetries:0'),
        re.compile(r'code:"EPROVIDERCREDENTIAL"'),
        re.compile(r'_ccMultiProviderDeniedRequestFields'),
        re.compile(r'beta\.messages\.countTokens\(_ccOutbound\)'),
        re.compile(r"https://api\.kimi\.com/coding"),
        re.compile(r"https://api\.z\.ai/api/anthropic"),
        re.compile(r"https://api\.minimax\.io/anthropic"),
        re.compile(r"KimiCLI/1\.5"),
        re.compile(r"function _ccMultiProviderCatalogInfo\("),
        re.compile(r'if\(_ccCandidates\.length===1\)'),
        re.compile(r'model:[\w$]+(?:\.enum)?\(\[\.\.\.new Set\('),
        re.compile(
            r"\{label:[\w$]+,domain:_ccAttributionDomain\}="
            r"_ccMultiProviderAttribution\([\w$]+,_ccNativeAttributionLabel\)"
        ),
        re.compile(
            r"<noreply@\$\{_ccAttributionDomain\}>",
        ),
        re.compile(r"_ccProviderModel\.contextWindow"),
        re.compile(r"_ccProviderModel\.maxOutputTokens"),
        re.compile(r'==="auto"&&_ccMultiProviderCatalogInfo\('),
        re.compile(r"_ccMultiProviderPickerCatalog\(\)"),
        re.compile(r'CC_OPENAI_AVAILABLE'),
        re.compile(r'CC_OPENAI_PROXY_AUTH_TOKEN'),
        re.compile(r'"moonshot:kimi-k3":\{inputTokens:3,outputTokens:15'),
        re.compile(r'"zai:glm-5\.3-flash":\{inputTokens:0\.15,outputTokens:0\.5'),
        re.compile(r'"minimax:minimax-m3":\{inputTokens:0\.3,outputTokens:1\.2'),
        re.compile(r'function _ccMultiProviderToolAllowed\('),
        re.compile(
            r'\.filter\(\([\w$]+\)=>_ccMultiProviderToolAllowed\('
            r'[\w$]+,[\w$]+\)\)\)?\.map\('
        ),
    ),
    min_version=_V_2_1_174,
    max_version=(2, 1, 275),
    requires_version=True,
)


# --- Catppuccin Macchiato syntax highlighting (2.1.151+) ---------------------
#
# The dark "Monokai" token scope map is a hardcoded `new Map([["keyword",...]])`
# literal not reachable via the theme overrides system. Recolor it to Catppuccin
# Macchiato. Length-free: no padding, and we keep every entry (the byte-patch
# version dropped `title.function` only to fit the original byte budget).
_SYNTAX_DARK_MAP: tuple[tuple[str, int, int, int], ...] = (
    ("keyword", 249, 38, 114),
    ("_storage", 102, 217, 239),
    ("built_in", 166, 226, 46),
    ("type", 166, 226, 46),
    ("literal", 190, 132, 255),
    ("number", 190, 132, 255),
    ("string", 230, 219, 116),
    ("title", 166, 226, 46),
    ("title.function", 166, 226, 46),
    ("title.class", 166, 226, 46),
    ("title.class.inherited", 166, 226, 46),
    ("params", 253, 151, 31),
    ("comment", 117, 113, 94),
    ("meta", 117, 113, 94),
    ("attr", 166, 226, 46),
    ("attribute", 166, 226, 46),
    ("variable", 255, 255, 255),
    ("variable.language", 255, 255, 255),
    ("property", 255, 255, 255),
    ("operator", 249, 38, 114),
    ("punctuation", 248, 248, 242),
    ("symbol", 190, 132, 255),
    ("regexp", 230, 219, 116),
    ("subst", 248, 248, 242),
)
_CATPPUCCIN_MACCHIATO: dict[str, tuple[int, int, int]] = {
    "keyword": (198, 160, 246),
    "_storage": (198, 160, 246),
    "built_in": (237, 135, 150),
    "type": (238, 212, 159),
    "literal": (245, 169, 127),
    "number": (245, 169, 127),
    "string": (166, 218, 149),
    "title": (138, 173, 244),
    "title.function": (138, 173, 244),
    "title.class": (238, 212, 159),
    "title.class.inherited": (238, 212, 159),
    "params": (238, 153, 160),
    "comment": (110, 115, 141),
    "meta": (198, 160, 246),
    "attr": (238, 212, 159),
    "attribute": (238, 212, 159),
    "variable": (202, 211, 245),
    "variable.language": (237, 135, 150),
    "property": (202, 211, 245),
    "operator": (145, 215, 227),
    "punctuation": (147, 154, 183),
    "symbol": (237, 135, 150),
    "regexp": (245, 189, 230),
    "subst": (202, 211, 245),
}


def _syntax_scope_pattern() -> re.Pattern[str]:
    parts: list[str] = []
    for i, (scope, r, g, b) in enumerate(_SYNTAX_DARK_MAP):
        var = rf"({_ID})" if i == 0 else r"\1"
        parts.append(rf'\["{re.escape(scope)}",{var}\({r},{g},{b}\)\]')
    return re.compile(r"new Map\(\[" + ",".join(parts) + r"\]\)")


def _syntax_scope_replacement(m: re.Match[str]) -> str:
    var = m.group(1)
    entries = ",".join(
        f'["{scope}",{var}'
        f"({_CATPPUCCIN_MACCHIATO[scope][0]},"
        f"{_CATPPUCCIN_MACCHIATO[scope][1]},"
        f"{_CATPPUCCIN_MACCHIATO[scope][2]})]"
        for scope, *_ in _SYNTAX_DARK_MAP
    )
    return f"new Map([{entries}])"


CATPPUCCIN_SYNTAX = PatchSet(
    name="catppuccin-syntax-scopes",
    patches=(
        Patch(
            name="catppuccin-syntax-scopes",
            pattern=_syntax_scope_pattern(),
            replacement=_syntax_scope_replacement,
        ),
    ),
    verify_present=(re.compile(r'new Map\(\[\["keyword",[\w$]+\(198,160,246\)'),),
    min_version=_V_2_1_151,
)


# --- thinking summaries in non-interactive sessions (2.1.151+) ---------------
#
# `showThinkingSummaries` (settings) drives the request's thinking.display, but
# the binary only applies it as the default when the session is interactive:
# `else if(!isInteractive()&&showThinkingSummaries())n8.display="summarized"`.
# Drop the interactive gate so the setting governs `display` in -p / other
# non-interactive sessions too -- otherwise `claude -p` requests omitted
# thinking and the on-disk transcript has no summaries. An explicit
# --thinking-display still wins (it is checked in the preceding branch).
THINKING_SUMMARIES_NONINTERACTIVE = PatchSet(
    name="thinking-summaries-noninteractive",
    patches=(
        Patch(
            name="ungate-thinking-display-default",
            pattern=re.compile(
                rf'else if\(!{_ID}\(\)&&({_ID}\(\))\)({_ID})\.display="summarized"'
            ),
            replacement=r'else if(\1)\2.display="summarized"',
        ),
    ),
    verify_present=(re.compile(r'else if\([\w$]+\(\)\)[\w$]+\.display="summarized"'),),
    verify_absent=(
        re.compile(r'else if\(![\w$]+\(\)&&[\w$]+\(\)\)[\w$]+\.display="summarized"'),
    ),
    min_version=_V_2_1_151,
)


# 2.1.198 selects the default in a helper and omits non-interactive text/quiet
# JSON thinking. Let the setting take priority, but keep explicit display choices.
THINKING_SUMMARIES_NONINTERACTIVE_198 = PatchSet(
    name=THINKING_SUMMARIES_NONINTERACTIVE.name,
    patches=(
        Patch(
            name="ungate-thinking-display-default",
            pattern=re.compile(
                rf"(function {_ID}\(\{{explicitDisplay:(?P<explicit>{_ID}),"
                rf"isNonInteractive:(?P<noninteractive>{_ID}),outputFormat:{_ID},"
                rf"verbose:{_ID}\}}\)\{{if\((?P=explicit)\)return (?P=explicit);)"
                rf'if\(!(?P=noninteractive)\)return (?P<setting>{_ID})\(\)\?"summarized":void 0;'
            ),
            replacement=r'\1if(\g<setting>())return"summarized";if(!\g<noninteractive>)return;',
        ),
        # The new subagent filter must not undo the setting for synchronous agents.
        Patch(
            name="preserve-configured-subagent-thinking",
            pattern=re.compile(
                rf'(function {_ID}\((?P<thinking>{_ID}),\{{useExactTools:{_ID},'
                rf'forwardSubagentText:{_ID},isAsync:{_ID},isNonInteractiveSession:{_ID},'
                rf'sessionDisplayExplicit:{_ID}\}}\)\{{if\()'
            ),
            replacement="",
            identifiers=(
                re.compile(
                    rf'function (?P<setting>{_ID})\(\)\{{return {_ID}\(\)\.showThinkingSummaries\?\?!1\}}'
                ),
            ),
            bound_replacement=lambda match, bindings: (
                match.group(0) + bindings["setting"] + "()||"
            ),
        ),
    ),
    verify_present=(
        re.compile(rf'if\({_ID}\(\)\)return"summarized";if\(!{_ID}\)return;'),
    ),
    min_version=(2, 1, 198),
    max_version=(2, 1, 275),
    requires_version=True,
)


# Model-facing description of the injected compact_session tool. The build serializes
# every tool for each API request by reading its `prompt()` method (see gtf/hI8 in the
# bundle); a tool that omits `prompt()` throws `H.prompt is not a function` on *every*
# message, so -- like every real tool -- we supply prompt() (model-facing text),
# description(), and a searchHint. The tool constructor copies own-properties only
# (it does not synthesize prompt() from description()), so all three must be explicit.
_COMPACT_DESC = (
    "Schedule compaction of this session: summarize the conversation so far to free "
    "up context. Runs at the end of the current turn if compaction is enabled and "
    "healthy; any in-flight work in this turn completes first. Use proactively when "
    "context is filling up instead of waiting for the automatic threshold."
)
# Short hint shown in CLAUDE_CODE_SIMPLE mode (gtf() returns searchHint when set).
_COMPACT_HINT = "schedule end-of-turn compaction to free up context"


def _compact_tool_object(schema_factory: str, *, use_factory: bool = False) -> str:
    # Use the native object factory, either a namespace member or an imported alias.
    return (
        '{name:"compact_session",'
        f'searchHint:"{_COMPACT_HINT}",'
        f'async description(){{return"{_COMPACT_DESC}"}},'
        f'async prompt(){{return"{_COMPACT_DESC}"}},'
        f"get inputSchema(){{return {schema_factory}({{}})}},"
        # the generic tool-use renderer calls H.tool.renderToolUseMessage(...)
        # UNCONDITIONALLY (no ?.), and it is not an aK/base default -- omitting it
        # throws `undefined(...)` on any transcript render of the tool. null == no
        # custom render line, matching TodoWrite's renderToolUseMessage(){return null}.
        "renderToolUseMessage(){return null},"
        "isReadOnly(){return!0},isConcurrencySafe(){return!0},"
        + ("create(){return{" if use_factory else "")
        + "async call(H,$){let W=Date.now(),Z=globalThis.__ccLastSelfCompact||0;"
        "if(W-Z<3e5){let Q=Math.round((W-Z)/1e3);"
        "return{data:{message:`compact_session was called ${Q}s ago; "
        "not rescheduling within the 300s cooldown.`}}}"
        "return globalThis.__ccPendingCompact=!0,globalThis.__ccLastSelfCompact=W,"
        '{data:{message:"Compaction scheduled: runs at the end of this turn if compaction '
        "is enabled and healthy. Context will be summarized; in-flight work in this turn "
        'completes first."}}}' + ('}},' if use_factory else ',') +
        # the framework passes the result's `.data` payload here (map(t.data,id)), so
        # read H.message directly -- not H.data.message (that double-dip was the bug)
        "mapToolResultToToolResultBlockParam(H,$){"
        'return{tool_use_id:$,type:"tool_result",content:H.message}}}'
    )


def _define_compact_tool(m: re.Match[str]) -> str:
    # Preserve the native schema declaration and use its initialized object factory.
    schema_factory, builder = m.group(2), m.group(4)
    # Follow TodoWrite's contract in this module, not a same-named unrelated builder.
    contract = re.compile(r"\b(create|async call)\(").search(m.string, m.end())
    use_factory = contract is not None and contract.group(1) == "create"
    definition = _compact_tool_object(schema_factory, use_factory=use_factory)
    tool = f"globalThis.__ccCompactTool={builder}({definition})"
    # Reuse the native binding so this also works in a var declaration list.
    return f"{m.group(1)}{m.group('todo')}={tool},"


_COMPACT_REGISTRY = re.compile(
    rf"function {_ID}\(\)\{{let (?P<tools>{_ID})=(?P<registry>{_ID})\(\),"
    rf"(?P<enabled>{_ID})=(?P=tools)\.map\(\((?P<tool>{_ID})\)=>"
    rf"(?P=tool)\.isEnabled\(\)\);return (?P=tools)\.filter\("
    rf"\({_ID},(?P<index>{_ID})\)=>(?P=enabled)\[(?P=index)\]\)"
    rf"\.map\(\((?P<entry>{_ID})\)=>(?P=entry)\.name\)\}}"
    rf"function (?P=registry)\(\)\{{(?:let {_ID}={_ID}\(\);)?"
    rf"return\[(?={_ID},)"
    rf"|function (?P<registered>{_ID})\(\)\{{let {_ID}={_ID}\(\);return\["
    rf"(?={_ID},[\s\S]{{1,3000}}?\]\}}{_ID}\((?P=registered)\);)"
)


def _register_compact(m: re.Match[str]) -> str:
    # Preserve the collector and the optional DesignTool initializer.
    return (
        m.group(0) + "...(globalThis.__ccCompactTool?[globalThis.__ccCompactTool]:[]),"
    )


def _force_compact(m: re.Match[str]) -> str:
    # xXf is the *proactive* autocompact decision. It skips itself via this guard
    # (m.group(0)) whenever the threshold source is "auto" -- which is every model
    # except the two in SXf ({sonnet-4-6, opus-4-6}) at <1M context. Opus 4.8 (1M
    # window) and Haiku 4.5 (absent from SXf) both resolve to "auto", so the guard
    # returns before the verdict line where the flag was previously read, leaving
    # compact_session a silent no-op. Consume the pending-compact flag BEFORE the
    # guard so an explicit compact_session forces compaction on every model, then
    # fall through to the untouched guard for the normal token-threshold path.
    return (
        "if(globalThis.__ccPendingCompact)"
        "return globalThis.__ccPendingCompact=!1,!0;" + m.group(0)
    )


COMPACT_SESSION = PatchSet(
    name="compact-session-tool",
    patches=(
        Patch(
            "define-compact-session-tool",
            re.compile(
                r'(([\w$]+(?:\.object)?)\(\{oldTodos:[\w$]+\(\)\.describe\('
                r'"The todo list before the update"\),newTodos:[\w$]+\(\)\.describe\('
                r'"The todo list after the update"\)\}\)\),)(?=(?P<todo>[\w$]+)=([\w$]+)\(\{name:)'
            ),
            _define_compact_tool,
        ),
        Patch(
            "register-compact-session-in-toollist",
            _COMPACT_REGISTRY,
            _register_compact,
        ),
        Patch(
            "force-compaction-on-flag",
            # Match the native skip guard. Its middle predicate can take a normalized model.
            # Consume the flag before this guard, after the native safety checks.
            # The lookbehind prevents a second application.
            re.compile(
                r"(?<!=!1,!0;)if\([\w$]+\(\)&&!"
                r"(?:[\w$]+\((?:[\w$]+\([\w$]+\))?\)&&!)?"
                r"[\w$]+\([\w$]+,[\w$]+\)\)return!1"
            ),
            _force_compact,
        ),
    ),
    verify_present=(
        re.compile(r'globalThis\.__ccCompactTool=[\w$]+\(\{name:"compact_session"'),
        # the tool must expose prompt() -- API tool serialization (gtf) reads it, and
        # a tool without it throws `H.prompt is not a function` on every request
        re.compile(r'name:"compact_session".{0,600}async prompt\(\)\{return'),
        re.compile(
            r'\.\.\.\(globalThis\.__ccCompactTool\?\[globalThis\.__ccCompactTool\]:\[\]\),[\w$]+,'
        ),
        # the flag is consumed *before* the source=="auto" skip guard in xXf, so an
        # explicit compact_session reaches the compactor even on models that skip
        # proactive autocompact (opus-4-8 1M window, haiku absent from SXf)
        re.compile(
            r"if\(globalThis\.__ccPendingCompact\)return "
            r"globalThis\.__ccPendingCompact=!1,!0;if\([\w$]+\(\)&&!"
        ),
    ),
    verify_absent=(
        re.compile(r'The todo list after the update"\)\}\)\),[\w$]+=[\w$]+\(\{name:'),
        _COMPACT_REGISTRY,
        # not double-applied: the injected early-return is never immediately followed
        # by a second copy of itself
        re.compile(r"=!1,!0;if\(globalThis\.__ccPendingCompact\)return globalThis"),
    ),
    min_version=(2, 1, 170),
)


def _select_patch_variant(
    version: Version | None, base: PatchSet, variant: PatchSet
) -> PatchSet:
    """Select a known variant through its declared version boundaries."""
    return variant if version is not None and variant.applies_to(version) else base


def default_patch_sets(version: Version | None) -> list[PatchSet]:
    """The patch sets applied by ``ccpatch apply`` (order matters)."""
    return [
        thinking_expanded(version),
        CHANNELS_ENABLED,
        DEV_CHANNEL_INHERITANCE,
        agents_view_handoff(version),
        background_provider_environment(version),
        MULTI_PROVIDER_SDK,
        CATPPUCCIN_SYNTAX,
        _select_patch_variant(
            version,
            THINKING_SUMMARIES_NONINTERACTIVE,
            THINKING_SUMMARIES_NONINTERACTIVE_198,
        ),
        COMPACT_SESSION,
    ]
