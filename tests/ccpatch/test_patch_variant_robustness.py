"""Check captured registry anchors, source mutations, and variant boundaries."""

import re
from dataclasses import replace
from pathlib import Path

import pytest

from ccpatch.patches import (
    BACKGROUND_PROVIDER_ENV,
    BACKGROUND_PROVIDER_ENV_198,
    COMPACT_SESSION,
    THINKING_SUMMARIES_NONINTERACTIVE,
    THINKING_SUMMARIES_NONINTERACTIVE_198,
    Patch,
    PatchError,
    PatchSet,
    Version,
    _override_patches,
    background_provider_environment,
    default_patch_sets,
)

# Provenance: build/sweep-resume/{version}/{platform}/original.js.
_REGISTRIES = (
    ("2.1.182-linux-x64", "Zfo", "NW", "", "I3n", "i4n"),
    ("2.1.198-linux-x64", "yBo", "M4", "", "$tr", "RRl"),
    ("2.1.198-darwin-arm64", "yUo", "F4", "", "Btr", "BHl"),  # codespell:ignore yuo
    ("2.1.199-linux-x64", "P2o", "n3", "CUf", "frr", "xMl"),
    ("2.1.199-linux-arm64", "MHo", "n4", "x$m", "mrr", "ANl"),
    ("2.1.199-darwin-arm64", "L2o", "sq", "O$m", "_rr", "FLl"),
    ("2.1.199-darwin-x64", "PBo", "s5", "P2f", "yrr", "FPl"),
)
_SPREAD = "...(globalThis.__ccCompactTool?[globalThis.__ccCompactTool]:[]),"
_REGISTRATION = PatchSet("registry-test", (COMPACT_SESSION.patches[1],))


@pytest.mark.parametrize("pattern", [r"(?P<value>a)", r"(?P<value>a*)"])
@pytest.mark.parametrize("use_callable", [False, True])
def test_retained_matches_preserve_substitution_semantics(
    pattern: str, use_callable: bool
) -> None:
    compiled = re.compile(pattern)
    source = "aba"

    def replace_match(match: re.Match[str]) -> str:
        assert match.string == source
        return f"[{match['value']}]"

    replacement = replace_match if use_callable else r"[\g<value>]"
    patch = Patch(
        name="substitution",
        pattern=compiled,
        replacement=replacement,
        expected_matches=(len(list(compiled.finditer(source))),),
    )
    assert PatchSet("fixture", (patch,)).apply(source) == compiled.sub(
        replacement, source
    )


def _registry_fragment(
    collector: str, registry: str, loader: str, first: str, second: str
) -> str:
    initializer = f"let e={loader}();" if loader else ""
    return (
        f"function {collector}(){{let e={registry}(),t=e.map((n)=>n.isEnabled());"
        "return e.filter((n,r)=>t[r]).map((n)=>n.name)}"
        f"function {registry}(){{{initializer}return[{first},{second},...e?[e]:[]]}}"
    )


@pytest.mark.parametrize(
    ("label", "collector", "registry", "loader", "first", "second"),
    _REGISTRIES,
    ids=[row[0] for row in _REGISTRIES],
)
def test_captured_registry_prefix_preserved(
    label: str, collector: str, registry: str, loader: str, first: str, second: str
) -> None:
    source = _registry_fragment(collector, registry, loader, first, second)
    unrelated = "function unrelated(){return[first,second]}"
    assert _REGISTRATION.apply(unrelated + source) == unrelated + source.replace(
        "return[", "return[" + _SPREAD
    ), label
    with pytest.raises(PatchError, match="register-compact-session-in-toollist"):
        _REGISTRATION.apply(source + source)
    with pytest.raises(PatchError, match="register-compact-session-in-toollist"):
        _REGISTRATION.apply(_REGISTRATION.apply(source))
    assert COMPACT_SESSION.verify_absent[1].search(source)
    assert not COMPACT_SESSION.verify_absent[1].search(unrelated)


@pytest.mark.parametrize("registry", ["tools", "$tools", "tools$"])
def test_registered_tool_factory_preserved(registry: str) -> None:
    source = (
        f"function {registry}(){{let design=load();return[first,second,...design?[design]:[]]}}"
        f"register({registry});"
    )
    assert _REGISTRATION.apply(source) == source.replace("return[", "return[" + _SPREAD)
    with pytest.raises(PatchError, match="register-compact-session-in-toollist"):
        _REGISTRATION.apply(source.replace(f"register({registry})", "register(other)"))
    with pytest.raises(PatchError, match="register-compact-session-in-toollist"):
        _REGISTRATION.apply(source + source)
    with pytest.raises(PatchError, match="register-compact-session-in-toollist"):
        _REGISTRATION.apply(_REGISTRATION.apply(source))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("isEnabled()", "isVisible()"),
        ("n.name", "n.label"),
        ("t[r]", "other[r]"),
        ("e.filter", "other.filter"),
        ("function n3()", "function other()"),
        ("n.isEnabled()", "other.isEnabled()"),
    ],
    ids=["semantic-method", "semantic-name", "mask", "tools", "registry", "entry"],
)
def test_registry_anchor_rejects_mutations(old: str, new: str) -> None:
    source = _registry_fragment("P2o", "n3", "CUf", "frr", "xMl")
    with pytest.raises(PatchError, match="register-compact-session-in-toollist"):
        _REGISTRATION.apply(source.replace(old, new))


@pytest.mark.parametrize(
    ("version", "modern"),
    [
        (None, False),
        ((2, 1, 173), False),
        ((2, 1, 174), False),
        ((2, 1, 197), False),
        ((2, 1, 198), True),
        ((2, 1, 199), True),
        ((2, 1, 200), True),
        ((2, 1, 201), True),
        ((2, 1, 202), True),
        ((2, 1, 203), True),
        ((2, 1, 204), True),
        ((2, 1, 205), True),
        ((2, 1, 206), True),
        ((2, 1, 208), True),
        ((2, 1, 207), True),
        ((2, 1, 209), True),
        ((2, 1, 210), True),
        ((2, 1, 211), True),
    ],
)
def test_default_variant_boundaries(version: Version | None, modern: bool) -> None:
    selected = default_patch_sets(version)
    assert len(selected) == 9
    expected = BACKGROUND_PROVIDER_ENV_198 if modern else BACKGROUND_PROVIDER_ENV
    assert selected[4].name == expected.name
    if version in ((2, 1, 207), (2, 1, 208), (2, 1, 209), (2, 1, 210), (2, 1, 211)):
        obsolete = {
            "restore-provider-env-after-settings-initializer",
            "restore-provider-env-at-operational-entry",
            "restore-provider-env-after-delayed-settings",
        }
        retained = tuple(
            patch for patch in expected.patches if patch.name not in obsolete
        )
        assert selected[4].patches[: len(retained)] == retained
        names = {patch.name for patch in selected[4].patches}
        assert names.isdisjoint(obsolete)
        assert {
            "initialize-provider-before-native-settings",
            "filter-provider-settings-with-native-policy",
            "install-provider-native-policy-boundary",
            "preserve-provider-transport-in-pty-host",
            "reset-provider-initialization-on-spare-claim",
            "avoid-provider-reset-after-claimed-entry",
            "carry-provider-env-to-agents-fallback",
        } <= names
    else:
        assert selected[4].patches[: len(expected.patches)] == expected.patches
    assert selected[7] is (
        THINKING_SUMMARIES_NONINTERACTIVE_198
        if modern
        else THINKING_SUMMARIES_NONINTERACTIVE
    )
    if modern:
        assert all(patch_set.applies_to(version) for patch_set in selected)
    if version in ((2, 1, 207), (2, 1, 208), (2, 1, 209), (2, 1, 210), (2, 1, 211)):
        assert sum(len(patch_set.patches) for patch_set in selected) == 77
        assert selected[4].max_version == (2, 1, 275)
        assert BACKGROUND_PROVIDER_ENV_198.applies_to(version)
        assert THINKING_SUMMARIES_NONINTERACTIVE_198.applies_to(version)
        assert selected[4].applies_to((2, 1, 274))
        assert not selected[4].applies_to((2, 1, 275))
        assert selected[7].applies_to((2, 1, 274))
        assert not selected[7].applies_to((2, 1, 275))


@pytest.mark.parametrize("scoped", [False, True])
def test_native_policy_filter_preserves_scoped_launcher(scoped: bool) -> None:
    patch = next(
        patch
        for patch in background_provider_environment((2, 1, 208)).patches
        if patch.name == "filter-provider-settings-with-native-policy"
    )
    patch_set = PatchSet("native-policy-filter", (patch,))
    argument = "launcher(env,scope)" if scoped else "env"
    native = f"color(host(managed(other(socket({argument})),scope)))"
    source = f"function filter(env,scope){{return {native}}}"
    patched = patch_set.apply(source)
    assert (
        f"_ccProviderValidateManaged(env,scope);return _ccProviderFilterSettings({native},scope)"
        in patched
    )
    if scoped:
        with pytest.raises(PatchError):
            patch_set.apply(
                source.replace("launcher(env,scope)", "launcher(env,otherScope)")
            )
    with pytest.raises(PatchError):
        patch_set.apply(source + source)


def test_named_overrides_preserve_order_and_unchanged_patches() -> None:
    base = BACKGROUND_PROVIDER_ENV.patches
    modern = BACKGROUND_PROVIDER_ENV_198.patches
    assert [patch.name for patch in modern] == [patch.name for patch in base]
    changed = {
        "remove-provider-env-from-respawn-guard",
        "stop-persisting-provider-env",
        "remove-provider-env-from-respawn-options",
        "thread-provider-env-through-claimed-spare-frame",
    }
    for original, variant in zip(base, modern, strict=True):
        assert (original is variant) == (original.name not in changed)


def test_named_overrides_reject_missing_duplicate_and_misnamed_targets() -> None:
    patch = BACKGROUND_PROVIDER_ENV.patches[0]
    for source in ((), (patch, patch)):
        with pytest.raises(PatchError, match="expected one matching target"):
            _override_patches(source, {patch.name: patch})
    with pytest.raises(PatchError, match="expected one matching target"):
        _override_patches((patch,), {patch.name: replace(patch, name="wrong")})


_CAPTURE_ROOT = Path(__file__).resolve().parents[2] / "build" / "sweep-resume"
_CAPTURES = sorted(
    path
    for path in _CAPTURE_ROOT.glob("2.1.*/*/original.js")
    if tuple(map(int, path.parent.parent.name.split("."))) <= (2, 1, 211)
)


@pytest.mark.parametrize(
    "capture",
    [
        path
        for path in _CAPTURES
        if path.parent.parent.name in {"2.1.201", "2.1.202", "2.1.203"}
    ],
    ids=lambda path: str(path.relative_to(_CAPTURE_ROOT)),
)
def test_checkpoint_thinking_anchors_match_native_capture(capture: Path) -> None:
    source = capture.read_text()
    version = tuple(int(part) for part in capture.parent.parent.name.split("."))
    patch_set = default_patch_sets(version)[7]
    assert patch_set is THINKING_SUMMARIES_NONINTERACTIVE_198
    patched = patch_set.apply(source)
    assert patched != source
    with pytest.raises(PatchError, match="ungate-thinking-display-default"):
        patch_set.apply(patched)


@pytest.mark.parametrize(
    "capture",
    _CAPTURES,
    ids=[str(path.relative_to(_CAPTURE_ROOT)) for path in _CAPTURES],
)
def test_captured_compact_output_unchanged(capture: Path) -> None:
    source = capture.read_text()
    native = _REGISTRATION.apply(source)
    assert native.count(_SPREAD) == 1
    # Provenance: the pre-refactor registry matcher used for these captures.
    old_pattern = re.compile(
        r"function ([\w$]+)\(\)\{(?:let [\w$]+=[\w$]+\(\);)?return\[(?=[\w$]+,)"
    )
    expected, count = old_pattern.subn(lambda match: match.group(0) + _SPREAD, source)
    assert count == 1
    assert native == expected
