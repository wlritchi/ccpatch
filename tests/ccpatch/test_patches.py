"""Tests for the length-free JS patch framework and the thinking patch set."""

from __future__ import annotations

import re

import pytest

from ccpatch.patches import (
    _PROVIDER_ENV_RESPAWN_GUARD,
    BACKGROUND_PROVIDER_ENV,
    CHANNELS_ENABLED,
    THINKING_SUMMARIES_NONINTERACTIVE_198,
    Patch,
    PatchError,
    PatchSet,
    background_provider_environment,
    checked_replace,
    default_patch_sets,
    discover_identifiers,
    parse_version,
    thinking_expanded,
)


@pytest.mark.parametrize("release", range(198, 281))
def test_thinking_variant_selected_from_198(release: int) -> None:
    variant = default_patch_sets((2, 1, release))[7]
    assert variant is THINKING_SUMMARIES_NONINTERACTIVE_198
    assert variant.applies_to((2, 1, release))


@pytest.mark.parametrize("version", [(2, 1, 275), (2, 1, 280), (3, 0, 0)])
def test_carried_patches_fail_loudly_on_unrecognized_source(
    version: tuple[int, int, int],
) -> None:
    selected = default_patch_sets(version)
    assert len(selected) == 11
    assert selected[7] is THINKING_SUMMARIES_NONINTERACTIVE_198
    for patch_set in selected:
        assert patch_set.max_version is None
        assert patch_set.applies_to(version)
    with pytest.raises(PatchError, match="ungate-thinking-display-default"):
        selected[7].apply("unrecognized upstream source")


# A minified-ish snippet exercising all three thinking patches.
_RENDER = (
    'case"thinking":{if(!Ab&&!Cd)return null;'
    "return q.createElement(Xy,{addMargin:Mn,param:Pq,"
    "isTranscriptMode:Rs,verbose:Tu})}"
)
_GROUP = "a:null,fG=Oh===null?ob_(Yz):void 0;if(Kw){Kw.latestThinkingSummary=x"
_SOURCE = _RENDER + ";" + _GROUP


@pytest.mark.parametrize(
    ("source", "patterns", "error"),
    [
        ("unrelated", (r"anchor=(?P<name>[\w$]+)",), "expected one match, got 0"),
        (
            "anchor=$x;anchor=$x",
            (r"anchor=(?P<name>[\w$]+)",),
            "expected one match, got 2",
        ),
        ("anchor=", (r"anchor=(?P<name>[\w$]+)?",), "missing binding 'name'"),
        (
            "a=$x;b=$y",
            (r"a=(?P<name>[\w$]+)", r"b=(?P<name>[\w$]+)"),
            "conflicting binding 'name'",
        ),
    ],
)
def test_identifier_discovery_rejects_invalid_anchors(
    source: str, patterns: tuple[str, ...], error: str
) -> None:
    with pytest.raises(PatchError, match=error):
        discover_identifiers(source, tuple(re.compile(pattern) for pattern in patterns))


def test_identifier_discovery_merges_consistent_bindings() -> None:
    assert discover_identifiers(
        "a=$x;b=$x;c=Y$",
        (
            re.compile(r"a=(?P<name>[\w$]+)"),
            re.compile(r"b=(?P<name>[\w$]+);c=(?P<other>[\w$]+)"),
        ),
    ) == {"name": "$x", "other": "Y$"}
    assert discover_identifiers("anything", ()) == {}


def test_patch_replacement_receives_discovered_bindings() -> None:
    def replace(match: re.Match[str], bindings: dict[str, str]) -> str:
        return f"{bindings['callee']}({match.group('argument')})"

    patch_set = PatchSet(
        name="bound",
        patches=(
            Patch(
                name="call",
                pattern=re.compile(r"CALL\((?P<argument>[\w$]+)\)"),
                replacement="unused",
                identifiers=(re.compile(r"anchor=(?P<callee>[\w$]+)"),),
                bound_replacement=replace,
                expected_matches=(1, 2),
            ),
        ),
    )
    assert patch_set.apply("anchor=$fn;CALL(A);CALL(B$)") == "anchor=$fn;$fn(A);$fn(B$)"
    assert patch_set.apply("anchor=other$;CALL(C)") == "anchor=other$;other$(C)"


@pytest.mark.parametrize("bound", [False, True])
def test_cardinality_checked_before_discovery_or_callbacks(bound: bool) -> None:
    calls: list[str] = []

    def callback(match: re.Match[str]) -> str:
        calls.append(match.group())
        return "changed"

    def bound_callback(match: re.Match[str], bindings: dict[str, str]) -> str:
        return callback(match)

    patch = Patch(
        "unique",
        re.compile("target"),
        callback,
        identifiers=(re.compile("missing=(?P<name>x)"),),
        bound_replacement=bound_callback if bound else None,
    )
    with pytest.raises(PatchError, match=r"catalog: patch 'unique'.*got 2"):
        PatchSet("catalog", (patch,)).apply("target;target")
    assert calls == []


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        (
            "send-provider-env-over-socket",
            'send({proto:P,op:"dispatch",d:{...job,nonce:N},timeoutMs:5000,auth:await auth()}',
        ),
        (
            "acknowledge-provider-env-version",
            'return respond(socket,{ok:!0,op:operation,short:id,pid:worker.record.pid,',
        ),
    ],
)
@pytest.mark.parametrize("count", [1, 3])
def test_captured_two_site_contract_rejects_missing_or_extra_site(
    name: str, fragment: str, count: int
) -> None:
    # All available .182-.199 captures have exactly two sites for these edits.
    patch = next(p for p in BACKGROUND_PROVIDER_ENV.patches if p.name == name)
    with pytest.raises(PatchError, match=f"got {count}"):
        PatchSet("two-site", (patch,)).apply(";".join([fragment] * count))


def test_optional_patch_allows_absence_not_duplicates() -> None:
    patch = Patch("optional", re.compile("target"), "done", required=False)
    patches = PatchSet("catalog", (patch,))
    assert patches.apply("other") == "other"
    assert patches.apply("target") == "done"
    with pytest.raises(PatchError, match="got 2"):
        patches.apply("target;target")


@pytest.mark.parametrize("counts", [(), (0,), (-1,), (1, 0)])
def test_invalid_cardinalities_rejected(counts: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="positive cardinalities"):
        Patch("invalid", re.compile("x"), "y", expected_matches=counts)


@pytest.mark.parametrize("source", ["other", "target;target"])
def test_checked_replace_reports_context(source: str) -> None:
    with pytest.raises(PatchError, match="inner edit: expected 1 exact occurrences"):
        checked_replace(source, "target", "done", context="inner edit")


def test_checked_replace_handles_intentional_multisite() -> None:
    assert checked_replace("x;x", "x", "y", context="pair", count=2) == "y;y"
    with pytest.raises(PatchError):
        checked_replace("x", "", "y", context="empty")


def test_inner_failure_includes_patch_set_and_name() -> None:
    def callback(match: re.Match[str]) -> str:
        return checked_replace(match.group(), "absent", "new", context="inner")

    with pytest.raises(PatchError, match="catalog: patch 'edit': inner:"):
        PatchSet("catalog", (Patch("edit", re.compile("x"), callback),)).apply("x")


def test_parse_version() -> None:
    assert parse_version("2.1.170") == (2, 1, 170)
    assert parse_version("2.1.170-beta.1") == (2, 1, 170)


@pytest.mark.parametrize("factory", ["createElement", "jsx"])
@pytest.mark.parametrize(
    "guard_body", ["return null;", "{return null}", "{return null;}"]
)
def test_thinking_render_patches_apply(factory: str, guard_body: str) -> None:
    source = _SOURCE.replace("createElement", factory).replace(
        "return null;", guard_body
    )
    out = thinking_expanded((2, 1, 186)).apply(source)
    assert f"q.{factory}(Xy," in out
    assert "isTranscriptMode:true,verbose:true" in out
    assert out == source.replace(f"if(!Ab&&!Cd){guard_body}", "").replace(
        "isTranscriptMode:Rs,verbose:Tu", "isTranscriptMode:true,verbose:true"
    ).replace("?ob_(Yz):", "?void 0:")
    assert "===null?void 0:void 0" in out  # grouping neutralized


@pytest.mark.parametrize(
    "guard_body", ["return null;", "{return null}", "{return null;}"]
)
def test_thinking_guard_verification(guard_body: str) -> None:
    source = _SOURCE.replace("return null;", guard_body)
    patches = thinking_expanded((2, 1, 203))
    verification_only = PatchSet(
        "thinking-verification", (), verify_absent=patches.verify_absent
    )
    with pytest.raises(PatchError, match="forbidden marker"):
        verification_only.apply(source)


@pytest.mark.parametrize(
    "guard_body", ["{return null;extra()}", "{return null", "return null}"]
)
def test_thinking_guard_rejects_nonmatching_bodies(guard_body: str) -> None:
    source = _SOURCE.replace("return null;", guard_body)
    with pytest.raises(PatchError, match="drop-thinking-early-return"):
        thinking_expanded((2, 1, 203)).apply(source)


@pytest.mark.parametrize("default", ["", 'n?.bgIsolation==="default"?void 0:'])
def test_provider_persistence_preserves_default_isolation(default: str) -> None:
    patch = next(
        patch
        for patch in background_provider_environment((2, 1, 211)).patches
        if patch.name == "stop-persisting-provider-env"
    )
    isolation = f'H={default}t==="repl"?"none":n?.bgIsolation,'
    patched = PatchSet("persistence", (patch,)).apply(
        isolation + "I=n?.providerEnv??snapshot(),"
    )
    assert patched.startswith(isolation)
    assert "snapshot()" not in patched
    assert "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST" in patched


@pytest.mark.parametrize("checks", ["ffs!==null&&mfs!==null&&", ""])
@pytest.mark.parametrize("factory", ["q.createElement", "e"])
def test_thinking_preserves_specialized_renderer_before_visibility_guard(
    checks: str, factory: str
) -> None:
    renderer = (
        f"if({checks}ffs(nO)){{let BW;"
        "if(cache[34]!==margin)BW=R.jsx(mfs,{param:nO,addMargin:margin}),"
        "cache[34]=margin,cache[35]=BW;else BW=cache[35];return BW}"
    )
    source = _SOURCE.replace('case"thinking":{', 'case"thinking":{' + renderer).replace(
        "q.createElement", factory
    )
    patched = thinking_expanded((2, 1, 211)).apply(source)
    assert renderer in patched
    assert "if(!Ab&&!Cd)return null;" not in patched
    assert "isTranscriptMode:true,verbose:true" in patched


def test_version_gating_excludes_ungroup_below_2_1_151() -> None:
    # Below 2.1.151 the grouping patch is not included, so a source WITHOUT the
    # grouping pattern still applies cleanly (no required no-op failure).
    out = thinking_expanded((2, 1, 150)).apply(_RENDER)
    assert "isTranscriptMode:true,verbose:true" in out


def test_ungroup_required_above_2_1_151_fails_when_absent() -> None:
    with pytest.raises(PatchError, match="disable-thinking-grouping"):
        thinking_expanded((2, 1, 170)).apply(_RENDER)  # no grouping pattern present


def test_verify_absent_catches_silent_failure() -> None:
    # A patch that does nothing but claims to remove the guard must fail verify.
    bogus = PatchSet(
        name="bogus",
        patches=(
            Patch(
                name="noop", pattern=re.compile("ZZZ"), replacement="", required=False
            ),
        ),
        verify_absent=(re.compile("return null;"),),
    )
    with pytest.raises(PatchError, match="forbidden marker"):
        bogus.apply(_SOURCE)


def test_applies_to_version_bounds() -> None:
    ps = PatchSet(
        name="x", patches=(), min_version=(2, 1, 100), max_version=(2, 1, 200)
    )
    assert ps.applies_to((2, 1, 150))
    assert not ps.applies_to((2, 1, 99))
    assert not ps.applies_to((2, 1, 200))
    assert ps.applies_to(None)


@pytest.mark.parametrize(
    "source",
    [
        'state?.channelsEnabled!==!0;obj.flag("tengu_harbor",!1)',
        'obj.state?.channelsEnabled!==!0;flag("tengu_harbor",!1)',
    ],
)
def test_channels_enabled_rejects_member_expressions(source: str) -> None:
    with pytest.raises(PatchError):
        CHANNELS_ENABLED.apply(source)


@pytest.mark.parametrize("property_name", ["providerEnvironment", "providerEnv$"])
def test_provider_respawn_guard_preserves_other_properties(property_name: str) -> None:
    source = f"if(active||job$.{property_name})respawn()"
    assert _PROVIDER_ENV_RESPAWN_GUARD.sub("", source) == source


def test_provider_respawn_guard_matches_dollar_identifier_exactly() -> None:
    source = "if(active||job$.providerEnv)respawn()"
    match = _PROVIDER_ENV_RESPAWN_GUARD.search(source)
    assert match is not None
    assert match.group("job") == "job$"
    assert _PROVIDER_ENV_RESPAWN_GUARD.sub("", source) == "if(active)respawn()"
