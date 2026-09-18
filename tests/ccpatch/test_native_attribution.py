"""Run captured native Bash prompt serialization without starting Claude or the SDK."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from ccpatch.module_runtime import source_modules
from ccpatch.patches import (
    MULTI_PROVIDER_SDK,
    PatchError,
    _attribution_function,
    _discover_multi_provider_attribution,
    _thread_modern_attribution,
    _thread_multi_provider_attribution,
    _transform_attribution_serializer,
    default_patch_sets,
)

_ID = r"[A-Za-z_$][\w$]*"
_ROOT = Path(os.environ.get("CCPATCH_NATIVE_SOURCE_ROOT", "/tmp/ccpatch-sweep-2.1.182"))  # noqa: S108 - read-only captured release sources
_ANCHORS = [
    r"Co-Authored-By: \$\{[^}]+\} <noreply@anthropic\.com>",
    r"if\([^;]+CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION\)return null",
    r"commit:[^?]+\?`\$\{[^}]+\}\nClaude-Session:",
    r"if\([^;]+\)return [\w$]+\([\w$]+\);let [\w$]+=[\w$]+\(\),[\w$]+=\[\.\.\.",
    r"let [\w$]+=[\w$]+\(\)!==null,[\w$]+=[\w$]+\([\w$]+\),",
    r"- Interactive flags \(",
    r"# Committing changes with git",
    r'"inputJSONSchema"in [\w$]+&&[\w$]+\.inputJSONSchema\?`',
    r"if\(![\w$]+\(\)\)return [\w$]+\.prompt\([\w$]+\);",
]


def _function(source: str, position: int) -> tuple[str, str]:
    matches = list(re.finditer(rf"(?:async )?function ({_ID})\(", source[:position]))
    match = matches[-1]
    following = re.search(rf"(?:async )?function {_ID}\(", source[position:])
    assert following is not None
    text = source[match.start() : position + following.start()]
    text = re.split(r"\}var [\w$]", text, maxsplit=1)[0]
    if not text.endswith("}"):
        text += "}"
    return match[1], text


@pytest.mark.parametrize("binding", ["plain", "$leading", "trailing$"])
def test_function_capture_stops_before_variable(binding: str) -> None:
    source = f"function helper(){{return 1}}var {binding}=2;function next(){{}}"
    assert _function(source, source.index("return")) == (
        "helper",
        "function helper(){return 1}",
    )


def _captures(source: str) -> list[tuple[str, str]]:
    captures: list[tuple[str, str]] = []
    for anchor in _ANCHORS:
        matches = list(re.finditer(anchor, source))
        assert len(matches) == 1, (anchor, len(matches))
        captures.append(_function(source, matches[0].start() + 1))
    base = captures[0][0]
    wrapper = re.search(
        rf"function ({_ID})\(\)\{{let (?:"
        rf"{_ID}={_ID}\(\),{_ID}={re.escape(base)}\(\)|"
        rf"{_ID}={re.escape(base)}\(\),{_ID}={_ID}\(\));",
        source,
    )
    assert wrapper is not None
    captures.append(_function(source, wrapper.end()))
    prompt = re.search(
        r"async prompt\(\{model:[\w$]+,tools:[\w$]+\}\).*?\},isConcurrencySafe", source
    )
    assert prompt is not None
    captures.append(("prompt", prompt[0].removesuffix(",isConcurrencySafe")))
    return captures


@lru_cache(maxsize=4)
def _patched_native_source(path: Path) -> str:
    return MULTI_PROVIDER_SDK.apply(path.read_text())


@pytest.mark.parametrize(
    "architecture", ["linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"]
)
@pytest.mark.parametrize("background_disabled", [False, True])
def test_native_cached_bash_attribution(
    architecture: str, background_disabled: bool, tmp_path: Path
) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    path = _ROOT / architecture / "original.js"
    reference = Path(
        os.environ.get(
            "CCPATCH_NATIVE_BASELINE",
            "/tmp/ccpatch-sweep-2.1.182/linux-x64/original.js",  # noqa: S108 - read-only harness baseline
        )
    )
    if runtime is None or not path.is_file() or not reference.is_file():
        pytest.skip(
            "requires node/bun, CCPATCH_NATIVE_SOURCE_ROOT pristine sources, "
            "and the .182 Linux x64 harness baseline"
        )
    source = path.read_text()
    version = re.search(r'VERSION:"2\.1\.(\d+)"', source)
    if version is not None and int(version[1]) >= 212:
        test_modern_captured_attribution(int(version[1]), architecture, tmp_path)
        return
    originals = _captures(source)
    canonical = _captures(reference.read_text())
    footer_functions: list[tuple[str, str]] = []
    footer_names: dict[str, str] = {}
    footer = re.search(rf",{_ID}=({_ID})\(\),{_ID}=`Co-Authored-By:", originals[0][1])
    if footer is not None:
        helper = _attribution_function(source, footer[1])
        gate = re.search(rf'({_ID})\("tengu_pr_footer_surface_suffix",!1\)', helper)
        surface = re.search(rf"let {_ID}=({_ID})\(\);return", helper)
        url = re.search(rf"\[Claude Code\]\(\$\{{({_ID})\}}\)", helper)
        assert gate is not None and surface is not None and url is not None
        surface_helper = _attribution_function(source, surface[1])
        client = re.search(rf"switch\(({_ID})\(\)\)", surface_helper)
        assert client is not None
        footer_names = {
            footer[1]: "_nativePRFooter",
            surface[1]: "_nativePRSurface",
            gate[1]: "ct",
            client[1]: "_nativeClientKind",
            url[1]: "A2e",
        }
        footer_functions = [(footer[1], helper), (surface[1], surface_helper)]
        name, baseline = canonical[0]
        baseline, count = re.subn(
            r"`\\uD83E\\uDD16 Generated with \[Claude Code\]\(\$\{[\w$]+\}\)`",
            "_nativePRFooter()",
            baseline,
        )
        assert count == 1
        canonical[0] = name, baseline
    session_object = re.search(
        rf"if\(!{_ID}\)return {_ID};return {_ID}\({_ID},{_ID}\.url,({_ID})\({_ID}\)\)",
        originals[len(_ANCHORS)][1],
    )
    if session_object is not None:
        # Update the comparison fixture, not the captured runtime functions.
        name, baseline = canonical[1]
        for arguments, session_id in (
            ("e,t", "e"),
            ("e.bridgeSessionId,e.sessionIngressUrl", "e.bridgeSessionId"),
        ):
            before = f"return vb({arguments})"
            assert baseline.count(before) == 1
            baseline = baseline.replace(
                before, f"return{{url:vb({arguments}),sessionId:{session_id}}}"
            )
        canonical[1] = name, baseline
        name, baseline = canonical[2]
        assert baseline.startswith(f"function {name}(e,t){{return")
        baseline = baseline.replace(
            f"function {name}(e,t){{return",
            f"function {name}(e,t,n){{let r=n??t;return",
        )
        assert baseline.endswith("${t}`:t}}")
        canonical[2] = name, baseline.removesuffix("${t}`:t}}") + "${r}`:r}}"
        name, baseline = canonical[len(_ANCHORS)]
        assert baseline == "function npt(){let e=MJa(),t=SUp();return e?bUp(t,e):t}"
        canonical[len(_ANCHORS)] = (
            name,
            "function npt(){let e=SUp(),t=MJa();if(!t)return e;"
            "return bUp(e,t.url,_nativeSessionLabel(t))}",
        )
        originals.append(
            (session_object[1], _attribution_function(source, session_object[1]))
        )
        canonical.append(
            ("_nativeSessionLabel", "function _nativeSessionLabel(e){return null}")
        )
    template_helpers = re.search(
        rf"## Summary\n\$\{{({_ID})\(\)\}}\n\n## Test plan\n\$\{{({_ID})\(\)\}}",
        originals[6][1],
    )
    if template_helpers is not None:
        name, baseline = canonical[6]
        gate_name: str | None = None
        for helper_name, fixture_name, enabled, disabled in (
            (
                template_helpers[1],
                "_nativePRSummary",
                "<1-2 plain sentences: what this change does and why>",
                "<1-3 bullet points>",
            ),
            (
                template_helpers[2],
                "_nativePRTestPlan",
                "<1-2 lines: what you verified and how (commands run, behavior observed); note anything unverified>",
                "[Bulleted markdown checklist of TODOs for testing the pull request...]",
            ),
        ):
            helper = _attribution_function(source, helper_name)
            gate = re.fullmatch(
                rf"function {re.escape(helper_name)}\(\)\{{return ({_ID})\(\)\?"
                + re.escape(json.dumps(enabled) + ":" + json.dumps(disabled))
                + r"\}",
                helper,
            )
            assert gate is not None, helper
            assert gate_name is None or gate_name == gate[1]
            gate_name = gate[1]
            originals.append((helper_name, helper))
            canonical.append(
                (
                    fixture_name,
                    f"function {fixture_name}(){{return _nativePRGate()?"
                    + json.dumps(enabled)
                    + ":"
                    + json.dumps(disabled)
                    + "}",
                )
            )
            assert baseline.count(disabled) == 1
            baseline = baseline.replace(disabled, f"${{{fixture_name}()}}")
        assert gate_name is not None
        originals.append((gate_name, _attribution_function(source, gate_name)))
        canonical.append(("_nativePRGate", "function _nativePRGate(){return!1}"))
        canonical[6] = name, baseline
    names: dict[str, str] = {}
    local_names: dict[str, dict[str, str]] = {}
    for index, ((_, original), (_, baseline)) in enumerate(
        zip(originals, canonical, strict=True)
    ):
        if index == len(_ANCHORS) and session_object is None:
            base = originals[0][0]
            original = re.sub(
                rf"let ({_ID}={re.escape(base)}\(\)),({_ID}={_ID}\(\));",
                r"let \2,\1;",
                original,
            )
        if index == 3 and '"tengu_relay_chain_v1"' not in original:
            # Release .198 removes the command-chaining guidance and its flag.
            guidance = re.search(
                rf',({_ID})={_ID}\("tengu_relay_chain_v1",!1\)\?\[\]:\[(.*?)\]\],',
                baseline,
            )
            if guidance is not None:
                prose = re.sub(rf"\$\{{{_ID}\}}", "${Bash}", guidance[2])
                assert prose == (
                    '"When issuing multiple commands:",['
                    '`If the commands are independent and can run in parallel, make multiple ${Bash} tool calls in a single message. Example: if you need to run "git status" and "git diff", send a single message with two ${Bash} tool calls in parallel.`,'
                    "`If the commands depend on each other and must run sequentially, use a single ${Bash} call with '&&' to chain them together.`,"
                    '"Use \';\' only when you need to run commands sequentially but don\'t care if earlier commands fail.",'
                    '"DO NOT use newlines to separate commands (newlines are ok in quoted strings)."'
                )
                baseline = (
                    baseline[: guidance.start()] + "," + baseline[guidance.end() :]
                )
                spread = f",...{guidance[1]},"
                assert baseline.count(spread) == 1
                baseline = baseline.replace(spread, ",")
                # The removed local shifts the remaining minified local names.
                local_names[originals[index][0]] = {}
        if index == 3 and re.search(
            rf'],{_ID}=!{_ID}\(\),{_ID}=\["Do not sleep between commands', original
        ):
            # Add the background-task gate to the fixture, not the captured code.
            sleep_array = re.search(
                rf'],({_ID})=\["Do not sleep between commands', baseline
            )
            assert sleep_array is not None
            baseline = (
                baseline[: sleep_array.start()]
                + "],_background=!_nativeBackgroundDisabled(),"
                + baseline[sleep_array.start() + 2 :]
            )
            monitor = (
                "Use the Monitor tool to stream events from a background process "
                "(each stdout line is a notification)."
            )
            before = (
                "['"
                + monitor
                + ' For one-shot "wait until done," use Bash with '
                + "run_in_background instead.']"
            )
            assert baseline.count(before) == 1
            baseline = baseline.replace(
                before, "[_background?" + before[1:-1] + ":" + json.dumps(monitor) + "]"
            )
            for guidance in (
                "If your command is long running and you would like to be notified when it finishes — use `run_in_background`. No sleep needed.",
                "If waiting for a background task you started with `run_in_background`, you will be notified when it completes — do not poll.",
            ):
                guidance = json.dumps(guidance)
                assert baseline.count(guidance) == 1
                baseline = baseline.replace(
                    guidance, "..._background?[" + guidance + "]:[]"
                )
            leading_sleep = re.search(
                rf'\.\.\.({_ID})\(\)\?\["Long leading `sleep` commands', baseline
            )
            assert leading_sleep is not None
            baseline = (
                baseline[: leading_sleep.start()]
                + leading_sleep[0].replace("()?", "()&&_background?", 1)
                + baseline[leading_sleep.end() :]
            )
            local_names[originals[index][0]] = {}
        # Normalize only known prose and call-site changes for name comparison.
        for before, after in (
            (
                "and for a completed change heading to a PR, only after the pre-ship checks below",
                "and for a completed change, per the pre-ship gate below",
            ),
            (',"bash_lean")', ")"),
            (',"bash_full")', ")"),
            ('("bash_lean")', "()"),
            ('("bash_full")', "()"),
        ):
            original = original.replace(before, after)
            baseline = baseline.replace(before, after)
        if index == 1 and "includeOutboundOnly:" in original:
            # Keep the new opt-in path in the captured runtime privacy tests.
            baseline = baseline.replace(
                f"function {canonical[index][0]}(){{",
                f"function {canonical[index][0]}({{includeOutboundOnly:_outbound=!1}}={{}}){{",
            )
            baseline = re.sub(
                rf"if\(({_ID})\(\)\)\{{let",
                r"if(\1()||_outbound){let",
                baseline,
            ).replace(".outboundOnly)", ".outboundOnly&&!_outbound)")
            local_names[originals[index][0]] = {}
        if index == 4 and re.search(rf",{_ID}={_ID}\(\),{_ID}=\[\];if\(!", original):
            # Keep the native minimal-prompt gate in the runtime fixture.
            avoidance = re.search(r'`- IMPORTANT:.*?`', baseline)
            cwd = re.search(r'"- Working directory persists.*?"', baseline)
            assert avoidance is not None and cwd is not None
            baseline = baseline.replace(
                'o=nC()?"`cat`',
                'o=_nativeMinimalBash(),_avoid=[];if(!o){let _commands=nC()?"`cat`',
            ).replace(
                '",s=[];if(t)',
                '";_avoid.push('
                + avoidance[0].replace('${o}', '${_commands}')
                + ')}let _cwd=o?"- Working directory persists between calls. Shell state (env vars, functions) does not persist; the shell is initialized from the user\'s profile.":'
                + cwd[0]
                + ',s=[];if(t)',
            )
            baseline = baseline.replace(
                cwd[0] + ',' + avoidance[0] + ',', '_cwd,..._avoid,'
            )
            baseline = (
                baseline.replace('let a="', 'let _commands="')
                .replace(')a+=', ')_commands+=')
                .replace('s.push(a)', 's.push(_commands)')
            )
            local_names[originals[index][0]] = {}
        if index == 7 and '"X:"' in original:
            # Compare the new strict-schema path without changing captured code.
            baseline = baseline.replace(
                'i="",a=o+s+""+',
                'i="",_strict=t.model&&_nativeStrict(t.model)?"X:":"",a=o+s+""+_strict+',
            ).replace('let d=ct("tengu_tool_pear",!1),f=', 'let f=')
            before = 'd&&e.strict===!0&&t.model&&eBe(t.model))c.strict=!0;'
            assert baseline.count(before) == 1
            baseline = baseline.replace(
                before,
                't.model&&_nativeStrict(t.model)){if(e.strict===!0){'
                'let _validated=_nativeValidate(f);if(_validated.ok)c.strict=!0,c.input_schema=_validated.schema;'
                'else _nativeWarn(`Tool ${e.name} has strict: true but its schema is not strict-compatible (${_validated.reason}); sending non-strict`,{level:"warn"})}'
                'else if(e.strictInputJSONSchema&&ct("tengu_structured_output_strict",!1))c.strict=!0,c.input_schema=e.strictInputJSONSchema}',
            )
            local_names[originals[index][0]] = {}
        tokens = re.findall(_ID, original)
        baseline_tokens = re.findall(_ID, baseline)
        assert len(tokens) == len(baseline_tokens), (
            index,
            len(tokens),
            len(baseline_tokens),
        )
        for token, baseline_token in zip(tokens, baseline_tokens, strict=True):
            if token != baseline_token:
                mapping = names
                if originals[index][0] in local_names and len(token) == 1:
                    # Separate lexical scopes can reuse the same minified local.
                    continue
                assert mapping.setdefault(token, baseline_token) == baseline_token
    for name, normalized in footer_names.items():
        assert names.setdefault(name, normalized) == normalized
    originals.extend(footer_functions)
    patched = _patched_native_source(path)
    for _, function in footer_functions:
        assert function in patched
    functions: list[str] = []
    for name, original in originals:
        if name == "prompt":
            start = patched.index(
                original[: original.index("}")] + ",_ccAttributionSnapshot"
            )
            text = patched[start : patched.index(",isConcurrencySafe", start)]
            functions.append(
                "const bash={name:'Bash',inputJSONSchema:{type:'object'}," + text + "};"
            )
        else:
            declaration = re.search(
                rf"(?:async )?function {re.escape(name)}\(", patched
            )
            assert declaration is not None
            functions.append(_function(patched, declaration.end())[1])
            inner = re.search(rf"async function {re.escape(name)}_ccInner\(", patched)
            if inner is not None:
                functions.append(_function(patched, inner.end())[1])
    helper_start = patched.index("const _ccMultiProviderDefinitions=")
    helper_end = patched.index("function ", helper_start)
    # The helper block ends at the first original function after its injected declarations.
    next_original = re.search(
        r"(?:async )?function (?!_cc)[\w$]+\(", patched[helper_end:]
    )
    assert next_original is not None
    helper = patched[helper_start : helper_end + next_original.start()]

    def normalize(text: str) -> str:
        # Keep model ID prefixes intact when a minified identifier is also a word.
        return re.sub(
            rf"{_ID}(?![\w$]|-Authored-By|-\d)",
            lambda match: names.get(match[0], match[0]),
            text,
        )

    payload = tmp_path / "native.json"
    payload.write_text(
        json.dumps(
            {
                "surfaceFooter": bool(footer_functions),
                "source": f"function _nativeBackgroundDisabled(){{return {json.dumps(background_disabled)}}}\n"
                + normalize(helper + "\n" + "\n".join(functions)),
            }
        )
    )
    result = subprocess.run(  # noqa: S603 - local runtime and regression harness
        [
            runtime,
            str(Path(__file__).with_name("native_attribution_regression.mjs")),
            str(payload),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "architecture", ["linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"]
)
def test_native_198_199_model_and_thinking_semantics(architecture: str) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    path = _ROOT / architecture / "original.js"
    if runtime is None or not path.is_file():
        pytest.skip("requires .198/.199 captured sources and node/bun")
    original = path.read_text()
    version_match = re.search(r'VERSION:"2\.1\.(198|199)"', original)
    if version_match is None:
        pytest.skip("requires .198/.199 captured sources")
    patched = original
    for patch_set in default_patch_sets((2, 1, int(version_match[1]))):
        patched = patch_set.apply(patched)

    def capture(source: str, anchor: str) -> tuple[str, str]:
        match = re.search(anchor, source)
        assert match is not None, anchor
        return _function(source, match.end())

    resolver_name, resolver = capture(original, r'if\(e\.agentType!==')
    cap_name = re.search(r'return (' + _ID + r')\(t\)\?', resolver)
    assert cap_name is not None
    _, cap = capture(original, rf'function {re.escape(cap_name[1])}\(')
    provider = re.search(r'if\((' + _ID + r')\(\)!=="firstParty"', cap)
    family = re.search(r'return!(' + _ID + r')\(e,t\)', cap)
    variables = re.search(
        r'let t=(' + _ID + r')\.slice\(0,\1\.indexOf\((' + _ID + r')\)', cap
    )
    definition = re.search(r'e\.agentType!==(' + _ID + r')\.agentType', resolver)
    assert provider and family and variables and definition
    _, family_source = capture(original, rf'function {re.escape(family[1])}\(')
    for text in (resolver, cap, family_source):
        assert text in patched
    agent_name, agent = capture(
        original, r'function ' + _ID + r'\(e,t,n,r,o\)\{let s=\(\)=>'
    )
    assert agent in patched
    inherit = re.search(r'let s=\(\)=>(' + _ID + r')\(', agent)
    default = re.search(r'let u=e\?\?(' + _ID + r')\(\)', agent)
    allow = re.search(r'if\(!(' + _ID + r')\(p\)\)', agent)
    region = re.search(r'let l=(' + _ID + r')\(t\)', agent)
    same = re.search(r'if\((' + _ID + r')\(n,t\)\)', agent)
    remap = re.search(r'let p=c\((' + _ID + r')\((' + _ID + r')\(n\)\)', agent)
    assert inherit and default and allow and region and same and remap
    display_name, display = capture(
        patched, r'explicitDisplay:' + _ID + r',isNonInteractive:'
    )
    thinking_name, thinking = capture(
        patched, r'sessionDisplayExplicit:' + _ID + r'\}\)\{'
    )
    setting = re.search(r'if\((' + _ID + r')\(\)\)return"summarized"', display)
    assert setting is not None
    dispatch = re.search(
        rf'(?P<payload>{_ID})=\{{CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST:'
        r'process\.env\.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST\}',
        patched,
    )
    assert dispatch is not None
    host_env = re.search(
        rf'env:\{{\.\.\.{dispatch["payload"]},(?P<host>\.\.\.{_ID}\('
        rf'{dispatch["payload"]}\.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST\)&&'
        rf'{_ID}\.CLAUDE_CODE_HOST_CREDS_FILE&&\{{CLAUDE_CODE_HOST_CREDS_FILE:'
        rf'{_ID}\.CLAUDE_CODE_HOST_CREDS_FILE\}})',
        patched,
    )
    assert host_env is not None
    host_check = re.search(r'\.\.\.(' + _ID + r')\(', host_env["host"])
    assert host_check is not None
    _, host_check_source = capture(original, rf'function {re.escape(host_check[1])}\(')
    host_expression = re.sub(
        rf'{_ID}\.CLAUDE_CODE_HOST_CREDS_FILE',
        'process.env.CLAUDE_CODE_HOST_CREDS_FILE',
        host_env["host"],
    )
    dispatch_script = (
        host_check_source
        + 'for(const value of [undefined,"0","1","true"]){'
        + 'if(value===undefined)delete process.env.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST;'
        + 'else process.env.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST=value;'
        + 'process.env.CLAUDE_CODE_HOST_CREDS_FILE="/host/creds";'
        + f'let {dispatch[0]};const env={{...{dispatch["payload"]},{host_expression}}};'
        + 'if((env.CLAUDE_CODE_HOST_CREDS_FILE!==undefined)!==["1","true"].includes(value))throw Error("host credentials");'
        + 'if(Object.keys(env).some(k=>!["CLAUDE_CODE_HOST_CREDS_FILE","CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"].includes(k)))throw Error("persisted provider env");}'
        + 'delete process.env.CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST;'
    )
    script = (
        dispatch_script + f'let provider="firstParty",enabled=false;'
        f'const {definition[1]}={{agentType:"Explore"}},{variables[1]}=["haiku","sonnet","opus"],{variables[2]}="opus";'
        f'function {provider[1]}(){{return provider}}'
        f'function {inherit[1]}(x){{return x.mainLoopModel}}'
        f'function {default[1]}(){{return "inherit"}}'
        f'function {allow[1]}(){{return true}}'
        f'function {region[1]}(){{return null}}'
        f'function {same[1]}(){{return false}}'
        f'function {remap[1]}(x){{return x}}'
        f'function {remap[2]}(x){{return x}}function {setting[1]}(){{return enabled}}'
        + family_source
        + resolver
        + cap
        + agent
        + display
        + thinking
        + f'const resolve={resolver_name},select={agent_name},display={display_name},thinking={thinking_name};'
        + '''
const explore={agentType:"Explore",source:"built-in",model:"inherit"};
delete process.env.CLAUDE_CODE_SUBAGENT_MODEL;
for(provider of ["firstParty","bedrock","vertex","foundry","gateway"])
for(const parent of ["claude-haiku-4-5","claude-sonnet-4-6","claude-opus-4-8","claude-fable-5","openai:gpt-6-astra","kimi:kimi-k3"]){
    const capped=provider==="firstParty"&&!/haiku|sonnet|opus/i.test(parent);
    if(resolve(explore,parent)!==(capped?"opus":"inherit"))throw Error("cap");
    if(select(resolve(explore,parent),parent)!==(capped?"opus":parent))throw Error("default explore");
    if(select(undefined,parent)!==parent)throw Error("default agent");
    for(const explicit of ["openai:gpt-6-astra","kimi:kimi-k3","inherit"]){
        if(select(resolve(explore,parent),parent,explicit)!==(explicit==="inherit"?parent:explicit))throw Error("explicit");
    }
    if(resolve({...explore,source:"user",model:"custom"},parent)!=="custom")throw Error("custom agent");
    process.env.CLAUDE_CODE_SUBAGENT_MODEL="zai:glm-5.3";
    if(select(resolve(explore,parent),parent,"opus")!=="zai:glm-5.3")throw Error("env override");
    delete process.env.CLAUDE_CODE_SUBAGENT_MODEL;
}
for(enabled of [false,true])
for(const explicitDisplay of [undefined,"omitted","summarized"])
for(const isNonInteractive of [false,true])
for(const outputFormat of ["text","json","stream-json"])
for(const verbose of [false,true]){
    const actual=display({explicitDisplay,isNonInteractive,outputFormat,verbose});
    const expected=explicitDisplay??(enabled?"summarized":isNonInteractive&&(outputFormat==="text"||outputFormat==="json"&&!verbose)?"omitted":undefined);
    if(actual!==expected)throw Error("display default");
    const options={isNonInteractiveSession:isNonInteractive,sessionDisplayExplicit:!!explicitDisplay};
    const disabled={type:"disabled"};
    if(thinking(disabled,options)!==disabled||"display" in disabled)throw Error("disabled");
    const result=thinking({type:"enabled",display:actual},options);
    const agentExpected=enabled||explicitDisplay||!isNonInteractive||actual==="omitted"?actual:"omitted";
    if(result.display!==agentExpected)throw Error("agent thinking");
}
'''
    )
    result = subprocess.run(  # noqa: S603 - local runtime and captured source
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


_ATTRIBUTION_SOURCE = 'function ATTR(){if(MODE()==="remote"){if(ENV.CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION)return{commit:"",pr:""};return REMOTE()}let H=CURRENT(),$=ISFIRST(H)?DISPLAY(FIRST.firstParty):ISNATIVE(H)?DISPLAY(H):"Claude",q=`\\uD83E\\uDD16 Generated with [Claude Code](${URL})`,K=`Co-Authored-By: ${$} <noreply@anthropic.com>`,_=SETTINGS();if(_.attribution)return{commit:_.attribution.commit??K,pr:_.attribution.pr??q};if(_.includeCoAuthoredBy===!1)return{commit:"",pr:""};return{commit:K,pr:q}}function COMPACT_GIT(F){if(!GIT())return"";let n="",{commit:r,pr:o}=ATTR();return r+o}function FULL_GIT(F){if(!GIT())return"";let n="",{commit:r,pr:o}=ATTR();return r+o}function COMPACT_BASH(F){return COMPACT_GIT(F)}function BASH_DISPATCH(M,F){if(SHORT(M))return COMPACT_BASH(F);return FULL_GIT(F)}var BASH={async prompt({model:M,tools:T}){let F=[];return BASH_DISPATCH(M,F)},isConcurrencySafe(){return!1}};async function SERIALIZE_NATIVE(E,T){let o="",s="",a=o+s+""+("inputJSONSchema"in E&&E.inputJSONSchema?`${E.name}:${HASH(E.inputJSONSchema)}`:E.name),l=CACHE(),c=l.get(a);return c}function _ccMultiProviderRoute(_ccNativeClient,_ccRequest,_ccOptions={}){delete _ccOutbound[_ccField];return[_ccCached.client,_ccOutbound,{}]}'


_MODERN_VERSIONS = [
    *range(212, 230),
    *range(231, 244),
    245,
    246,
    247,
    248,
    250,
    251,
    252,
    257,
    258,
    259,
    260,
    261,
    263,
    265,
    266,
    267,
    268,
    269,
    270,
    271,
    272,
    273,
    274,
]


@pytest.mark.parametrize(
    ("version", "architecture"),
    [
        (version, architecture)
        for version in _MODERN_VERSIONS
        for architecture in ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64")
        if (
            Path(__file__).resolve().parents[2]
            / f"build/sweep-resume/2.1.{version}/{architecture}/original.js"
        ).is_file()
    ]
    or [(272, "linux-x64")],
)
def test_modern_captured_attribution(
    version: int, architecture: str, tmp_path: Path
) -> None:
    root = Path(__file__).resolve().parents[2] / "build/sweep-resume"
    path = root / f"2.1.{version}/{architecture}/original.js"
    runtime = shutil.which("node")
    if not path.is_file() or runtime is None:
        pytest.skip("requires captured release and node")
    source = next(
        module.source
        for module in source_modules(path.read_text())
        if "# Committing changes with git" in module.source
    )
    route = _ATTRIBUTION_SOURCE[
        _ATTRIBUTION_SOURCE.index("function _ccMultiProviderRoute(") :
    ]
    generated = _thread_modern_attribution(source + route)
    check = subprocess.run(  # noqa: S603 - local syntax check
        [runtime, "--input-type=module", "--check"],
        input=generated,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert check.returncode == 0, check.stderr
    assert "if(!_ccSnapshot)return _ccBlocks" in generated
    assert "Symbol.for(\"ccpatch.attribution\")" in generated
    # Policy awaits and replay branches must remain byte-identical.
    for match in re.finditer(
        r'await [\w$]+(?:\.attributionTextOf)?\("(?:commit|pr)",[\w$]+\.(?:commit|pr)\)',
        source,
    ):
        assert match.group(0) in generated
    for match in re.finditer(
        r'function [\w$]+\(\)\{return [\w$]+\(\)\?null:[\w$]+\(\)\}', source
    ):
        assert match.group(0) in generated
    serializer = re.search(
        r'async function ([\w$]+)\(([\w$]+),([\w$]+)\)\{return _ccAttributionScope.run',
        generated,
    )
    assert serializer is not None
    name, tool, context = serializer.groups()
    end = generated.index(f"async function {name}_ccInner", serializer.start())
    wrapper = generated[serializer.start() : end]
    effective = re.search(r'\?await ([\w$]+)\(\):void 0', wrapper)
    assert effective is not None
    system_helper = _attribution_function(
        generated, "_ccMultiProviderSystemAttribution"
    )
    original_declaration = re.search(rf"async function {re.escape(name)}\(", source)
    assert original_declaration is not None
    original_serializer = _function(source, original_declaration.end())[1]
    inner = _function(generated, end + len(f"async function {name}_ccInner("))[1]
    assert (
        inner.replace(name + "_ccInner", name, 1).replace(
            f"+JSON.stringify([{context}.model??null,{context}._ccAttributionSnapshot??null])",
            "",
            1,
        )
        == original_serializer
    )
    prompts: list[str] = []
    prompt_names: list[str] = []
    for anchor in ("- Interactive flags (", "# Committing changes with git"):
        prompt_name, prompt_source = _function(generated, generated.index(anchor))
        prompts.append(prompt_source)
        prompt_names.append(prompt_name)
    payload = tmp_path / "modern.json"
    payload.write_text(
        json.dumps(
            {
                "modern": True,
                "source": "\n".join(prompts),
                "prompts": prompt_names,
                "systemHelper": system_helper,
                "effective": effective[1],
            }
        )
    )
    prompt_result = subprocess.run(  # noqa: S603 - captured native Git prompts
        [
            runtime,
            str(Path(__file__).with_name("native_attribution_regression.mjs")),
            str(payload),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert prompt_result.returncode == 0, prompt_result.stdout + prompt_result.stderr
    script = (
        system_helper
        + 'for(const snapshot of [null,undefined,{commit:"",pr:""}]){if(_ccMultiProviderSystemAttribution("native","zai:test",snapshot).length!==1)throw Error("suppression")}'
        + 'let custom=_ccMultiProviderSystemAttribution("native","zai:test",{commit:"custom commit",pr:"custom PR"});if(!custom[1].text.includes("custom commit")||!custom[1].text.includes("custom PR"))throw Error("custom attribution");'
        + 'const {AsyncLocalStorage}=require("node:async_hooks");const _ccAttributionScope=new AsyncLocalStorage;'
        'let calls=0;'
        f'async function {effective[1]}(){{calls++;let model=_ccAttributionScope.getStore().model;await Promise.resolve();return model==="replay"?null:{{commit:model,pr:"native PR"}}}}'
        f'async function {name}_ccInner({tool},{context}){{await Promise.resolve();return {{name:{tool}.name,description:JSON.stringify({context}._ccAttributionSnapshot)}}}}'
        + wrapper
        + f'(async()=>{{let results=await Promise.all(["zai:a","openai:b","replay"].map(model=>{name}({{name:"Bash"}},{{model}})));'
        'if(calls!==3)throw Error("policy calls");for(let i=0;i<results.length;i++){let value=results[i][Symbol.for("ccpatch.attribution")];'
        'if(i===2){if(value!==null)throw Error("replay")}else if(value.commit!==["zai:a","openai:b"][i]||!Object.isFrozen(value))throw Error("isolation");'
        'if(JSON.stringify(results[i]).includes("ccpatch.attribution"))throw Error("wire leak");}})().catch(e=>{console.error(e);process.exitCode=1})'
    )
    result = subprocess.run(  # noqa: S603 - captured local wrapper
        [runtime, "-e", script], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def _patch_attribution(source: str) -> str:
    match = re.fullmatch(r"[\s\S]*", source)
    assert match is not None
    return _thread_multi_provider_attribution(match)


@pytest.mark.parametrize("strict_prefix", ["", 'strict+'])
@pytest.mark.parametrize("schema_method", [False, True])
def test_attribution_cache_key_retains_native_dimensions(
    strict_prefix: str, schema_method: bool
) -> None:
    source = _ATTRIBUTION_SOURCE.replace('o+s+""+', 'o+s+""+' + strict_prefix)
    if schema_method:
        source = source.replace(
            "HASH(E.inputJSONSchema)", "state.schemaKey(E.inputJSONSchema)"
        )
        source = source.replace("l=CACHE(),c=l.get(a)", "c=sharedCache.get(a)")
    found = _discover_multi_provider_attribution(source)
    generated = _patch_attribution(source)
    assert (
        found.serializer.group("key_expr")
        + '+JSON.stringify([T.model??null,T._ccAttributionSnapshot??null])'
    ) in generated


@pytest.mark.parametrize(
    "version", ["2.1.202", "2.1.203", "2.1.206", "2.1.207", "2.1.208", "2.1.209"]
)
@pytest.mark.parametrize("architecture", ["linux-x64", "linux-arm64"])
def test_captured_sdk_serializer_strict_cache(
    version: str, architecture: str, tmp_path: Path
) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    path = Path(__file__).resolve().parents[2] / "build/sweep-resume" / version
    path /= architecture + "/original.js"
    if runtime is None or not path.is_file():
        pytest.skip("requires node/bun and captured .202/.203/.206/.207 sources")
    source = path.read_text()
    found = _discover_multi_provider_attribution(source)
    patched = MULTI_PROVIDER_SDK.apply(source)
    assert found.serializer.group("key_expr") in patched
    serializer = found.serializer
    name = serializer.group("serialize")
    original = _function(source, serializer.start() + len(f"async function {name}("))[1]
    transformed = _transform_attribution_serializer(source, found)
    start = transformed.index(f"async function {name}(")
    inner_start = transformed.index(f"async function {name}_ccInner(")
    wrapper = transformed[start:inner_start]
    inner = _function(
        transformed, inner_start + len(f"async function {name}_ccInner(")
    )[1]
    # Keep the native serializer body intact except for the added cache dimension.
    assert (
        inner.replace(name + "_ccInner", name, 1).replace(
            '+JSON.stringify([t.model??null,t._ccAttributionSnapshot??null])', "", 1
        )
        == original
    )
    calls = set(re.findall(r"(?<![\w$.])(" + _ID + r")\(", original))
    calls.discard(name)
    calls -= {"if", "Set"}
    cache_fn = re.search(rf'{serializer.group("cache")}=({_ID})\(\)', original)
    assert cache_fn is not None
    provider = re.search(r"let " + _ID + r"=(" + _ID + r")\(\)", original)
    assert provider is not None
    description = re.search(r"description:await (" + _ID + r")\(", original)
    assert description is not None
    strict = re.search(r't\.model&&(' + _ID + r')\(t\.model\)\?"X:"', original)
    validator = re.search(
        r"let " + _ID + r"=(" + _ID + r")\([\w$]+\);if\([\w$]+\.ok\)", original
    )
    if version in {"2.1.203", "2.1.206", "2.1.207", "2.1.208", "2.1.209"}:
        assert strict is not None and validator is not None
    stubs = "".join(f"function {call}(){{return false}}" for call in sorted(calls))
    stubs += (
        f"function {cache_fn[1]}(){{return cache}}"
        f'function {provider[1]}(){{return "firstParty"}}'
        f"async function {description[1]}(tool,context){{await Promise.resolve();"
        'return context._ccAttributionSnapshot?.commit??"native"}'
        f"function {found.effective}(model){{return {{commit:model+suffix,pr:suffix}}}}"
    )
    if strict is not None and validator is not None:
        stubs += (
            f"function {strict[1]}(){{return strictEnabled}}"
            f"function {validator[1]}(schema){{return {{ok:compatible,schema:{{...schema,converted:true}}}}}}"
        )
    script = (
        'const assert=require("node:assert/strict");'
        'let cache=new Map(),strictEnabled=false,compatible=true,suffix="A";'
        'const _ccAttributionKey=Symbol("attribution");'
        + stubs
        + wrapper
        + inner
        + f"const serialize={name};"
        + '''
(async()=>{
    const tool={name:"Bash",inputJSONSchema:{type:"object"},strict:true};
    const first=serialize(tool,{model:"zai:glm-5.3"});
    suffix="B";
    const second=serialize(tool,{model:"openai:gpt-6-astra"});
    const [a,b]=await Promise.all([first,second]);
    assert.equal(a.description,"zai:glm-5.3A");
    assert.equal(b.description,"openai:gpt-6-astraB");
    assert.equal(a[_ccAttributionKey].commit,a.description);
    assert.equal(b[_ccAttributionKey].commit,b.description);
    assert(!JSON.stringify(a).includes("attribution"));
    const hit=await serialize(tool,{model:"openai:gpt-6-astra"});
    assert.deepEqual(hit,b);
    strictEnabled=true;
    const strictSchema=await serialize(tool,{model:"openai:gpt-6-astra"});
'''
        + (
            'assert.equal(strictSchema.strict,true);'
            'assert.equal(strictSchema.input_schema.converted,true);'
            'assert.equal(cache.size,3);'
            'compatible=false;suffix="C";'
            'const invalid=await serialize(tool,{model:"openai:gpt-6-astra"});'
            'assert.equal(invalid.strict,undefined);'
            if strict is not None
            else 'assert.equal(cache.size,2);'
        )
        + '})().catch(e=>{console.error(e);process.exitCode=1});'
    )
    script_path = tmp_path / "serializer.cjs"
    script_path.write_text(script)
    result = subprocess.run(  # noqa: S603 - captured serializer and local runtime
        [runtime, str(script_path)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("helper", ["FOOTER", "$footer", "footer$"])
def test_attribution_retains_native_pr_expression(helper: str) -> None:
    template = (
        "`"
        + chr(92)
        + "uD83E"
        + chr(92)
        + "uDD16 Generated with [Claude Code](${URL})`"
    )
    source = _ATTRIBUTION_SOURCE.replace(template, helper + "()")
    assert source != _ATTRIBUTION_SOURCE
    assert _patch_attribution(source) == _patch_attribution(
        _ATTRIBUTION_SOURCE
    ).replace(template, helper + "()")


@pytest.mark.parametrize(
    "expression", ["FOOTER(model)", "obj.footer()", "FOOTER()+extra"]
)
def test_attribution_unknown_pr_expression_fails_closed(expression: str) -> None:
    template = (
        "`"
        + chr(92)
        + "uD83E"
        + chr(92)
        + "uDD16 Generated with [Claude Code](${URL})`"
    )
    with pytest.raises(PatchError, match="multi-provider attribution"):
        _patch_attribution(_ATTRIBUTION_SOURCE.replace(template, expression))


def test_attribution_generated_output_is_unchanged() -> None:
    generated = _patch_attribution(_ATTRIBUTION_SOURCE)
    assert hashlib.sha256(generated.encode()).hexdigest() == (
        "ba3ae7c0bfda28ae2d689249989de7a9f16f8260659b034a644e5b6f219b799a"
    )


@pytest.mark.parametrize(
    "separator", ["\n", "\nconst unused=0;", "\nlet unused=0;", "class Boundary{}"]
)
def test_attribution_function_boundaries(separator: str) -> None:
    source = _ATTRIBUTION_SOURCE.replace(
        "}function COMPACT_BASH", "}" + separator + "function COMPACT_BASH"
    )
    assert _patch_attribution(source) == _patch_attribution(
        _ATTRIBUTION_SOURCE
    ).replace("}function COMPACT_BASH", "}" + separator + "function COMPACT_BASH")


def test_attribution_reordered_section_properties() -> None:
    source = _ATTRIBUTION_SOURCE.replace("{commit:r,pr:o}", "{pr:o,commit:r}")
    assert _patch_attribution(source) == _patch_attribution(
        _ATTRIBUTION_SOURCE
    ).replace("{commit:r,pr:o}", "{pr:o,commit:r}")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("function ATTR(){", ""),
        ("function ATTR(){", "function ATTR(x){"),
        ("return FULL_GIT(F)", "return FULL_GIT(F)+FULL_GIT(F)"),
        (
            "return FULL_GIT(F)",
            "if(F){FULL_GIT(F)}let x=0;return FULL_GIT(F)",
        ),
        (
            "return COMPACT_GIT(F)",
            "if(F){COMPACT_GIT(F)}const x=0;return COMPACT_GIT(F)",
        ),
        ("return COMPACT_GIT(F)", "return COMPACT_GIT(F)+FULL_GIT(F)"),
        ("return r+o", "return ATTR()+r+o"),
        ("return COMPACT_BASH(F)", "return COMPACT_BASH(F)+COMPACT_BASH(F)"),
    ],
)
def test_attribution_syntax_mutations_fail_closed(old: str, new: str) -> None:
    with pytest.raises(PatchError, match="multi-provider attribution"):
        _patch_attribution(_ATTRIBUTION_SOURCE.replace(old, new))


@pytest.mark.parametrize("version", ["2.1.199", "2.1.200"])
def test_captured_attribution_session_wrapper_runtime(version: str) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    path = Path(__file__).resolve().parents[2] / "build/sweep-resume" / version
    path /= "linux-x64/original.js"
    if runtime is None or not path.is_file():
        pytest.skip("requires node/bun and captured .199/.200 sources")
    source = path.read_text()
    found = _discover_multi_provider_attribution(source)
    assert found.wrapper is not None
    wrapper = found.wrapper
    link = re.search(rf",({_ID})=({_ID})\(\);", wrapper)
    assert link is not None
    append = re.search(
        rf"return ({_ID})\(" if version == "2.1.200" else rf"\?({_ID})\(", wrapper
    )
    assert append is not None
    functions = _attribution_function(source, append[1])
    if version == "2.1.200":
        label = re.search(rf"\.url,({_ID})\(", wrapper)
        assert label is not None
        label_body = _attribution_function(source, label[1])
        assert re.fullmatch(rf"function {_ID}\({_ID}\)\{{return null\}}", label_body)
        functions += label_body.replace(
            "return null", 'events.push("label");return null'
        )
    patched = wrapper.replace(
        f"{found.effective}()", f"{found.effective}(_ccAttributionModel)"
    ).replace(f"{found.base_name}()", f"{found.base_name}(_ccAttributionModel)")
    synthetic = _ATTRIBUTION_SOURCE.replace(
        "=ATTR();", f"={found.effective}();"
    ).replace(
        "function COMPACT_GIT",
        wrapper.replace(f"{found.base_name}()", "ATTR()") + "function COMPACT_GIT",
    )
    assert patched.replace(f"{found.base_name}(", "ATTR(") in _patch_attribution(
        synthetic
    )
    script = (
        'const assert=require("node:assert/strict");let events=[],session=null;'
        f'function {found.base_name}(model){{events.push(model);return {{commit:model,pr:"PR"}}}}'
        f'function {link[2]}(){{events.push("link");return session}}'
        + functions
        + patched
        + f'let absent={found.effective}("zai:glm-5.3");'
        + 'assert.deepEqual(absent,{commit:"zai:glm-5.3",pr:"PR"});'
        + 'assert.deepEqual(events,["zai:glm-5.3","link"]);events=[];'
        + (
            'session={url:"https://session",sessionId:"id"};'
            if version == "2.1.200"
            else 'session="https://session";'
        )
        + f'let present={found.effective}("openai:gpt-5.6-sol");'
        + 'assert.deepEqual(present,{commit:"openai:gpt-5.6-sol\\nClaude-Session: https://session",pr:"PR\\n\\nhttps://session"});'
        + 'assert.deepEqual(events,'
        + json.dumps(
            ["openai:gpt-5.6-sol", "link"] + (["label"] if version == "2.1.200" else [])
        )
        + ');'
    )
    result = subprocess.run(  # noqa: S603 - local runtime and captured attribution functions
        [runtime, "-e", script], capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        "if(link)return value;return SESSION(value,link.url,LABEL(link))",
        "if(!link)return link;return SESSION(value,link.url,LABEL(link))",
        "if(!link)return value;return SESSION(value,link.url,LABEL(value))",
        "if(!link)return value;return SESSION(value,link.uri,LABEL(link))",
    ],
)
def test_attribution_session_object_wrapper_fails_closed(mutation: str) -> None:
    wrapper = f"function EFFECTIVE(){{let value=ATTR(),link=LINK();{mutation}}}"
    source = _ATTRIBUTION_SOURCE.replace("=ATTR();", "=EFFECTIVE();").replace(
        "function COMPACT_GIT", wrapper + "function COMPACT_GIT"
    )
    with pytest.raises(PatchError, match="session-link wrapper changed"):
        _patch_attribution(source)


@pytest.mark.parametrize("reverse", [False, True])
def test_attribution_wrapper_binding_order_is_preserved(reverse: bool) -> None:
    bindings = "value=ATTR(),link=LINK()" if reverse else "link=LINK(),value=ATTR()"
    wrapper = (
        f"function EFFECTIVE(){{let {bindings};return link?SESSION(value,link):value}}"
    )
    source = _ATTRIBUTION_SOURCE.replace("=ATTR();", "=EFFECTIVE();").replace(
        "function COMPACT_GIT", wrapper + "function COMPACT_GIT"
    )
    generated = _patch_attribution(source)
    assert (
        wrapper.replace("EFFECTIVE()", "EFFECTIVE(_ccAttributionModel)").replace(
            "ATTR()", "ATTR(_ccAttributionModel)"
        )
        in generated
    )
