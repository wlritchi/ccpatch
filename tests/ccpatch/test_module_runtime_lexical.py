"""Reject literal module edges and retain repeated export aliases."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch.module_runtime import (
    ModuleRuntimeError,
    ensure_module_reference,
    module_reference,
    source_modules,
)


def _graph(owner: str, consumer: str) -> str:
    return "".join(
        f"\n/* ccpatch-module:{name.encode().hex()} */\n{body}"
        for name, body in (("a.mjs", owner), ("b.mjs", consumer))
    )


@pytest.mark.parametrize(
    "fake",
    [
        "const text='import{x}from\"./a.mjs\"';",
        'const text="import{x}from\'./a.mjs\'";',
        '/* import{x}from"./a.mjs" */',
        '// import{x}from"./a.mjs"\n',
        'const text=`import{x}from"./a.mjs"`;',
        'const text=`${{a:`nested ${1}`}.a}import{x}from"./a.mjs"`;',
        'const pattern=/import{x}from"a.mjs"/;',
        'if(true)/import{x}from"a.mjs"/.test("");',
        'if(true){} /import{x}from"a.mjs"/.test("");',
        'const pattern=/[import{x}from"a.mjs"]/;',
    ],
)
def test_fake_import_cannot_resolve_or_create_edge(fake: str) -> None:
    graph = _graph("let x=1;export{x};", fake + "const consumer=1;")
    args = (graph, graph.index("let x"), "x", graph.index("const consumer"))
    with pytest.raises(ModuleRuntimeError, match="no unique import"):
        module_reference(*args)
    with pytest.raises(ModuleRuntimeError, match="cannot add dependency edge"):
        ensure_module_reference(*args)


@pytest.mark.parametrize(
    "fake",
    [
        'const text="export{x}";',
        "const text='export{x}';",
        '/* export{x} */',
        '// export{x}\n',
        'const text=`${{a:`nested`}.a}export{x}`;',
        'const pattern=/export{x}/;',
        'if(true)/export{x}/.test("");',
        'export{x}from"./other.mjs";',
        'export{x}/* not a local export */from"./other.mjs";',
    ],
)
def test_fake_export_does_not_resolve(fake: str) -> None:
    graph = _graph("let x=1;" + fake, 'import{x}from"./a.mjs";const consumer=1;')
    with pytest.raises(ModuleRuntimeError, match="no unique import"):
        module_reference(
            graph, graph.index("let x"), "x", graph.index("const consumer")
        )


@pytest.mark.parametrize(
    "prefix",
    [
        'const n=6/2/3;',
        'const n=function(){} / 2;',
        'const n=class{} / 2;',
        'class A{} /[{}]/.test("");',
        'function f(){} /[{}]/.test("");',
        'const f=()=>({a:1});const n=f().a/2;',
        'if(true)/[{}]/.test("{}");',
        'const text=`${{a:`nested ${1}`}.a}import{x}from"./a.mjs"`;',
    ],
)
def test_real_declarations_after_literals_and_division(prefix: str) -> None:
    graph = _graph(
        "let x=1;" + prefix + "export{x};", prefix + 'import{x}from"./a.mjs";'
    )
    owner, consumer = source_modules(graph)
    assert module_reference(graph, owner.start, "x", consumer.start) == "x"


def test_generated_binding_avoids_identifier_substrings() -> None:
    graph = _graph(
        "let x=1;export{};",
        'import{}from"./a.mjs";const prefix__ccpatchNativeBinding0suffix=1;',
    )
    owner, consumer = source_modules(graph)
    _, alias = ensure_module_reference(graph, owner.start, "x", consumer.start)
    assert alias == "__ccpatchNativeBinding1"


def test_repeated_import_aliases_remain_ambiguous() -> None:
    graph = _graph("let x=1;export{x};", 'import{x as first,x as second}from"./a.mjs";')
    owner, consumer = source_modules(graph)
    with pytest.raises(ModuleRuntimeError, match="no unique import"):
        module_reference(graph, owner.start, "x", consumer.start)


def test_comments_between_declaration_tokens() -> None:
    graph = _graph(
        "let x=1;export/* export{fake} */{x /* alias */ as first};",
        'import/* import{fake} */{first as value}/* edge */from"./a.mjs";',
    )
    owner, consumer = source_modules(graph)
    assert module_reference(graph, owner.start, "x", consumer.start) == "value"


@pytest.mark.parametrize("public", ["first", "second"])
def test_repeated_export_alias_retains_live_binding(
    tmp_path: Path, public: str
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for ESM execution")
    graph = _graph(
        "let x=1;function update(){x=2}export{x as first,x as second,update};",
        f'import{{{public} as value,update}}from"./a.mjs";'
        'update();console.log(PLACEHOLDER);',
    )
    owner, consumer = source_modules(graph)
    unchanged, alias = ensure_module_reference(graph, owner.start, "x", consumer.start)
    assert unchanged == graph
    assert alias == "value"
    for module in source_modules(graph.replace("PLACEHOLDER", alias)):
        (tmp_path / module.name).write_text(module.source)
    result = subprocess.run(  # noqa: S603 - Execute the local ESM fixture.
        [node, str(tmp_path / "b.mjs")], check=True, capture_output=True, text=True
    )
    assert result.stdout.strip() == "2"
