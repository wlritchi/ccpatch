"""Blessed configuration forwarding for the left-arrow agents view."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .module_runtime import register_module_bootstrap

if TYPE_CHECKING:
    from .patches import PatchSet, Version

_ID = r"[\w$]+"
_HANDOFF = re.compile(
    rf'(?<![\w$])(?P<gate>{_ID}\("tengu_bg_leftarrow_inprocess",!0\)\)try\{{return await '
    rf'(?P<mount>{_ID})\((?P<job>{_ID}),(?P<load>{_ID}))'
    rf'(?P<options>,\{{(?:\.\.\.(?P<restricted>{_ID})\(\)&&\{{dispatchExtraArgs:\["--restricted"\]\}},)?dispatchDefaults:(?P<defaults>{_ID})'
    rf'(?:,\.\.\.(?P<selection>{_ID})\?\.autoOpenJobId!==void 0&&'
    rf'\{{autoOpenJobId:(?P=selection)\.autoOpenJobId\}})?'
    rf'(?:,originSpawn:{_ID})?(?:,storageV5:{_ID})?(?:,credentials:{_ID})?'
    rf'(?:,fleetNudgeStore:(?P=selection)\?\.fleetNudgeStore)?\}})?'
    rf'(?P<middle>\)\}}catch\((?P<error>{_ID})\)\{{{_ID}\((?P=error)\)\}}'
    rf'(?:return |let {_ID}=await )'
    rf'{_ID}\(\{{args:\["agents"(?:,\.\.\.{_ID}\((?P=defaults)\))?)'
    rf'(?P<end>\],env:\{{CLAUDE_AGENTS_SELECT:'
    rf'(?(selection)(?P=selection)\?\.autoOpenJobId\?\?)'
    rf'(?P=job),\.\.\.{_ID}\(\)'
    rf'(?:,CLAUDE_CODE_PROVIDER_ENV_TRANSIENT:JSON\.stringify\({_ID}\(\)\))?'
    rf'(?:,\.\.\.(?P=restricted)\(\)&&\{{CLAUDE_CODE_RESTRICTED:"1"\}})?\}}\}}\))'
)
_GETTER = re.compile(
    rf'function (?P<getter>{_ID})\(\)\{{return {_ID}(?:(?:\(\))?\.host\.launchOptions)?'
    rf'\.replConfigArgv(?:\(\))?\}}'
)
_PARSER = re.compile(
    rf'function (?P<parser>{_ID})\({_ID}\)\{{let {_ID}=!1,{_ID},'
    rf'{_ID}=\{{addDir:\[\],pluginDir:\[\],'
)
_SERIALIZER = re.compile(
    rf'function (?P<serializer>{_ID})\((?P<config>{_ID})\)'
    rf'\{{return\[\.\.\.(?P=config)\.settings\?\["--settings",'
)
_LEGACY_MOUNT = re.compile(
    rf'(?P<prefix>async function {_ID}\({_ID},{_ID})(?P<body>\)\{{'
    rf'let {_ID}={_ID}\(\)\.catch\(\(\)=>\[\]\);'
    rf'.{{0,1100}}?"\[PERF:bg-leftarrow-mounted\]"\),await {_ID}\({_ID})'
    rf'(?P<end>\),await {_ID}\(0,"other",\{{suppressResumeHint:!0\}}\),'
    rf'process.exit\(0\)\}})'
)


def _helper(match: re.Match[str]) -> str:
    # Import here to avoid a cycle with the patch catalog.
    from .patches import discover_identifiers

    source = match.group(0)
    captures = discover_identifiers(source, (_GETTER, _PARSER, _SERIALIZER))
    if '/* ccpatch-module:' not in source:
        getter = _GETTER.search(source)
        assert getter is not None
        return (
            source[: getter.end()]
            + (
                'function _ccAgentsDispatchArgs(){return '
                f'{captures["serializer"]}({captures["parser"]}({captures["getter"]}()??[]).config)'
                '}'
            )
            + source[getter.end() :]
        )
    runtime = 'globalThis.__ccpatchRuntime.agentsHandoff'
    edits: list[tuple[int, str]] = []
    for pattern, name in (
        (_GETTER, "getter"),
        (_PARSER, "parser"),
        (_SERIALIZER, "serializer"),
    ):
        definition = pattern.search(source)
        assert definition is not None
        edits.append((definition.start(), f'{runtime}.{name}={captures[name]};'))
    for offset, text in sorted(edits, reverse=True):
        source = source[:offset] + text + source[offset:]
    source = source.replace('_ccAgentsDispatchArgs()', f'{runtime}.dispatchArgs()')
    return register_module_bootstrap(
        source,
        'agentsHandoff',
        'return {dispatchArgs(){return this.serializer(this.parser(this.getter()??[]).config)}}',
    )


def _handoff(match: re.Match[str]) -> str:
    options = match.group("options")
    if options:
        restricted = match.group("restricted")
        extra = (
            f'[...({restricted}()?["--restricted"]:[]),..._ccAgentsDispatchArgs()]'
            if restricted
            else '_ccAgentsDispatchArgs()'
        )
        options = options[:-1] + f',dispatchExtraArgs:{extra}}}'
    else:
        options = ',{dispatchExtraArgs:_ccAgentsDispatchArgs()}'
    return (
        match.group("gate")
        + options
        + match.group("middle")
        + ',..._ccAgentsDispatchArgs()'
        + match.group("end")
    )


def _launch(match: re.Match[str]) -> str:
    # Import here to avoid a cycle with the patch catalog.
    from .patches import PatchError

    source = match.group(0)
    matches = [
        handoff
        for anchor in re.finditer('"tengu_bg_leftarrow_inprocess"', source)
        if (
            handoff := _HANDOFF.search(
                source, max(0, anchor.start() - 100), anchor.end() + 10000
            )
        )
        is not None
    ]
    if len(matches) != 1:
        raise PatchError(f"expected one agents handoff, got {len(matches)}")
    handoff = matches[0]
    return source[: handoff.start()] + _handoff(handoff) + source[handoff.end() :]


def agents_view_handoff(version: Version | None) -> PatchSet:
    # Import here to avoid a cycle with the patch catalog.
    from .patches import Patch, PatchSet

    legacy = version is not None and version < (2, 1, 195)
    return PatchSet(
        name="agents-view-handoff",
        patches=(
            Patch(
                "agents-channels-serialized-once",
                re.compile(
                    rf',\.\.\.\({_ID}\.channels\?\?\[\]\)\.flatMap\('
                    rf'\((?P<entry>{_ID})\)=>\["--channels",(?P=entry)\]\)'
                ),
                "",
                required=version is None or version >= (2, 1, 187),
            ),
            Patch("agents-launch-config", re.compile(r"\A[\s\S]+\Z"), _launch),
            Patch("agents-config-helper", re.compile(r"\A[\s\S]+\Z"), _helper),
            *(
                (
                    Patch(
                        "agents-legacy-mount-config",
                        _LEGACY_MOUNT,
                        lambda m: (
                            m.group("prefix")
                            + ',_ccOptions'
                            + m.group("body")
                            + ',_ccOptions'
                            + m.group("end")
                        ),
                    ),
                )
                if legacy
                else ()
            ),
        ),
        verify_present=(
            re.compile(
                r'dispatchExtraArgs:(?:\[\.\.\.\([\w$]+\(\)\?\["--restricted"\]:\[\]\),\.\.\.)?(?:_ccAgentsDispatchArgs|globalThis\.__ccpatchRuntime\.agentsHandoff\.dispatchArgs)\(\)'
            ),
        ),
        min_version=(2, 1, 182),
        max_version=(2, 1, 275),
        requires_version=True,
    )
