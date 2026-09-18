"""Execute split-module bootstrap and native-alias regressions."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ccpatch import patches
from ccpatch.module_runtime import (
    ModuleRuntimeError,
    ensure_module_reference,
    finalize_module_runtime,
    module_reference,
    register_module_bootstrap,
    source_modules,
)


def _graph(modules: dict[str, str]) -> str:
    return "".join(
        f"\n/* ccpatch-module:{name.encode().hex()} */\n{body}"
        for name, body in modules.items()
    )


def _execute(tmp_path: Path, source: str, entry: str) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for ESM execution")
    for module in source_modules(source):
        (tmp_path / module.name).write_text(module.source)
    result = subprocess.run(  # noqa: S603 - Execute the local test fixture.
        [node, str(tmp_path / entry)], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def test_shared_capture_precedes_settings_and_waits_for_claim(tmp_path: Path) -> None:
    graph = _graph(
        {
            "entry.mjs": 'import "./settings.mjs";import "./snapshot.mjs";'
            'const s=globalThis.__ccpatchRuntime.provider;'
            'if(s.value!=="requester"||s.restored)throw Error("premature restore");'
            's.awaiting=false;s.restore();console.log(JSON.stringify(s));',
            "settings.mjs": 'import "./cycle.mjs";const s=globalThis.__ccpatchRuntime.provider;'
            's.events.push("settings");s.restore();',
            "cycle.mjs": 'import "./settings.mjs";'
            'globalThis.__ccpatchRuntime.provider.events.push("cycle");',
            "snapshot.mjs": 'globalThis.__ccpatchRuntime.provider.events.push("snapshot");',
        }
    )
    graph = register_module_bootstrap(
        graph,
        "provider",
        'return {value:"requester",awaiting:true,restored:false,'
        'captures:(globalThis.captures=(globalThis.captures??0)+1),events:["capture"],'
        'restore(){if(!this.awaiting){this.restored=true;this.events.push("restore")}}};',
    )
    result = _execute(tmp_path, finalize_module_runtime(graph), "entry.mjs")
    assert result["captures"] == 1
    assert result["restored"] is True
    assert result["events"] == ["capture", "cycle", "settings", "snapshot", "restore"]


def test_bootstrap_payload_bounds_identifier_runs() -> None:
    code = 'return {value:"' + "x" * 20000 + '"};'
    registered = register_module_bootstrap("native();", "shared", code)
    payload = registered.split("ccpatch-bootstrap:", 1)[1].split(" */", 1)[0]
    assert max(map(len, payload.split())) <= 64
    assert json.loads(bytes.fromhex(payload)) == ["shared", code]
    assert code in finalize_module_runtime(registered)


def test_finalization_preserves_many_module_boundaries() -> None:
    bodies = {f"module-{index}.mjs": f"const value={index};" for index in range(200)}
    graph = register_module_bootstrap(_graph(bodies), "shared", "return {};")
    finalized = source_modules(finalize_module_runtime(graph))
    assert len(finalized) == len(bodies)
    for module, (name, body) in zip(finalized, bodies.items(), strict=True):
        assert module.name == name
        assert module.source.endswith("\n" + body)
        assert module.source.count("globalThis.__ccpatchRuntime??=") == 1
        assert "ccpatch-bootstrap:" not in module.source


def test_catalog_helper_available_in_dependency_before_owner(tmp_path: Path) -> None:
    graph = _graph(
        {
            "entry.mjs": 'import {value} from "./context.mjs";console.log(JSON.stringify({value}));',
            "context.mjs": 'export const value=globalThis.__ccpatchRuntime.catalog.info("custom");',
        }
    )
    graph = register_module_bootstrap(
        graph, "catalog", 'return {info(model){return model==="custom"?200000:0}};'
    )
    assert _execute(tmp_path, finalize_module_runtime(graph), "entry.mjs") == {
        "value": 200000
    }


def test_native_alias_resolves_collision_and_executes(tmp_path: Path) -> None:
    graph = _graph(
        {
            "native.mjs": 'const HOr=["sonnet"];export {HOr as models};',
            "entry.mjs": 'import {models as nativeModels} from "./native.mjs";'
            'const HOr="marketplace";console.log(JSON.stringify({value:PLACEHOLDER}));',
        }
    )
    name = module_reference(
        graph,
        graph.index('const HOr=["sonnet"]'),
        "HOr",
        graph.index('const HOr="marketplace"'),
    )
    assert name == "nativeModels"
    assert _execute(tmp_path, graph.replace("PLACEHOLDER", name), "entry.mjs") == {
        "value": ["sonnet"]
    }


def test_missing_import_fails_instead_of_using_colliding_local() -> None:
    graph = _graph({"a.mjs": "const x=1;export{x};", "b.mjs": "const x=2;"})
    with pytest.raises(ModuleRuntimeError, match="no unique import"):
        module_reference(graph, graph.index("const x=1"), "x", graph.index("const x=2"))


def test_bootstrap_registration_order_and_conflict(tmp_path: Path) -> None:
    source = _graph(
        {
            "entry.mjs": 'console.log(JSON.stringify(globalThis.__ccpatchRuntime.second));'
        }
    )
    source = register_module_bootstrap(source, "first", 'return {value:42};')
    source = register_module_bootstrap(
        source, "second", 'return {value:globalThis.__ccpatchRuntime.first.value};'
    )
    assert _execute(tmp_path, finalize_module_runtime(source), "entry.mjs") == {
        "value": 42
    }
    source = register_module_bootstrap(source, "first", 'return {value:0};')
    with pytest.raises(ModuleRuntimeError, match="conflicting"):
        finalize_module_runtime(source)


def test_transport_capture_once_and_mutable_claim_state(tmp_path: Path) -> None:
    graph = _graph(
        {
            "entry.mjs": 'import "./settings.mjs";import "./snapshot.mjs";'
            'const s=globalThis.__ccpatchRuntime.worker;'
            's.payload={key:"claimed"};s.awaiting=false;s.restore();'
            'console.log(JSON.stringify({captures:s.captures,value:process.env.CC_TEST_KEY,'
            'initial:s.initial,deleted:!("CC_TEST_TRANSPORT" in process.env)}));',
            "settings.mjs": 'const s=globalThis.__ccpatchRuntime.worker;'
            's.initial=s.payload.key;s.restore();'
            'if(process.env.CC_TEST_KEY!==undefined)throw Error("restore before claim");',
            "snapshot.mjs": 'if(globalThis.__ccpatchRuntime.worker.payload.key!=="requester")'
            'throw Error("capture overwritten");',
        }
    )
    graph = register_module_bootstrap(
        graph,
        "worker",
        'const raw=process.env.CC_TEST_TRANSPORT;delete process.env.CC_TEST_TRANSPORT;'
        'return {payload:JSON.parse(raw),awaiting:true,captures:1,'
        'restore(){if(!this.awaiting)process.env.CC_TEST_KEY=this.payload.key}};',
    )
    finalized = finalize_module_runtime(graph)
    (tmp_path / "launch.mjs").write_text(
        'process.env.CC_TEST_TRANSPORT=JSON.stringify({key:"requester"});'
        'delete process.env.CC_TEST_KEY;await import("./entry.mjs");'
    )
    assert _execute(tmp_path, finalized, "launch.mjs") == {
        "captures": 1,
        "value": "claimed",
        "initial": "requester",
        "deleted": True,
    }


def test_added_binding_retains_live_export_and_existing_edge(tmp_path: Path) -> None:
    graph = _graph(
        {
            "native.mjs": "let value=1;function update(){value=2}export{update};",
            "entry.mjs": 'import {update} from "./native.mjs";'
            'const __ccpatchNativeBinding0="occupied";update();'
            'console.log(JSON.stringify({value:PLACEHOLDER}));',
        }
    )
    graph, alias = ensure_module_reference(
        graph, graph.index("let value"), "value", graph.index("const __ccpatch")
    )
    assert alias == "__ccpatchNativeBinding1"
    assert _execute(tmp_path, graph.replace("PLACEHOLDER", alias), "entry.mjs") == {
        "value": 2
    }


def test_added_binding_preserves_existing_cycle(tmp_path: Path) -> None:
    graph = _graph(
        {
            "native.mjs": 'import {read} from "./entry.mjs";'
            'let value=42;export function run(){return read()}',
            "entry.mjs": 'import {run} from "./native.mjs";'
            'export function read(){return PLACEHOLDER}'
            'queueMicrotask(()=>console.log(JSON.stringify({value:run()})));',
        }
    )
    graph, alias = ensure_module_reference(
        graph, graph.index("let value"), "value", graph.index("export function read")
    )
    assert _execute(tmp_path, graph.replace("PLACEHOLDER", alias), "entry.mjs") == {
        "value": 42
    }


def test_added_binding_uses_transitive_edges_and_stays_live(tmp_path: Path) -> None:
    graph = _graph(
        {
            "native.mjs": 'globalThis.order=["native"];let value=1;'
            'function update(){value=2}export{update};',
            "bridge.mjs": 'import {update} from "./native.mjs";'
            'globalThis.order.push("bridge");export{update};',
            "entry.mjs": 'import {update} from "./bridge.mjs";'
            'globalThis.order.push("entry");update();'
            'console.log(JSON.stringify({value:PLACEHOLDER,order:globalThis.order}));',
        }
    )
    graph, alias = ensure_module_reference(
        graph,
        graph.index("let value"),
        "value",
        graph.index('globalThis.order.push("entry")'),
    )
    assert 'from "./native.mjs"' not in source_modules(graph)[2].source
    assert (
        module_reference(
            graph,
            graph.index("let value"),
            "value",
            graph.index('globalThis.order.push("entry")'),
        )
        == alias
    )
    assert _execute(tmp_path, graph.replace("PLACEHOLDER", alias), "entry.mjs") == {
        "value": 2,
        "order": ["native", "bridge", "entry"],
    }


def test_transitive_search_terminates_on_disconnected_cycle() -> None:
    graph = _graph(
        {
            "native.mjs": "const x=1;export{x};",
            "a.mjs": 'import {b} from "./b.mjs";export const a=()=>b;',
            "b.mjs": 'import {a} from "./a.mjs";export const b=()=>a;',
        }
    )
    with pytest.raises(ModuleRuntimeError, match="cannot add dependency edge"):
        ensure_module_reference(
            graph, graph.index("const x"), "x", graph.index("export const a")
        )


def test_added_binding_refuses_new_dependency_edge() -> None:
    graph = _graph({"a.mjs": "const x=1;export{x};", "b.mjs": "const y=2;"})
    with pytest.raises(ModuleRuntimeError, match="cannot add dependency edge"):
        ensure_module_reference(
            graph, graph.index("const x"), "x", graph.index("const y")
        )


@pytest.mark.parametrize("native_early_return", [False, True])
def test_sdk_bootstrap_precedes_owner_and_shares_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_early_return: bool
) -> None:
    monkeypatch.setattr(patches, "_MULTI_PROVIDER_AGENT_IDENTIFIERS", ())
    patchset = patches._SDKPatchSet(
        name="sdk-fixture", patches=(patches.MULTI_PROVIDER_SDK.patches[1],)
    )
    graph = _graph(
        {
            "entry.mjs": 'import {early} from "./early.mjs";'
            'import {factory} from "./native.mjs";'
            'process.env.CC_KIMI_AUTH_TOKEN="fixture";'
            'const native=await factory({});'
            'const a=_ccMultiProviderRoute(native,{model:"moonshot:kimi-k3"});'
            'const b=_ccMultiProviderRoute(native,{model:"moonshot:kimi-k3"});'
            'console.log(JSON.stringify({early,same:a[0]===b[0],'
            'model:a[1].model,native:_ccMultiProviderRoute(native,{model:"opus"})[0]===native}));',
            "early.mjs": 'let knowledge;const register=e=>knowledge=e,known=()=>false,identity=e=>e;'
            'register({isKnown:(e)=>known(identity(e,{identity:!0})),isModelId:(e)=>false});'
            'if(!knowledge.isKnown("openai:gpt-6-astra"))throw Error("provider unknown at startup");'
            'if(knowledge.isKnown("openai:future"))throw Error("unknown provider accepted");'
            'export const early=_ccMultiProviderCatalogInfo("moonshot:kimi-k3").contextWindow;',
            "native.mjs": 'class Native{constructor(options){this.options=options}}'
            'async function factory({apiKey}){let options={apiKey:null};return new Native(options)}'
            'async function next(){}export {factory};',
        }
    )
    if native_early_return:
        graph = graph.replace(
            "async function factory({apiKey}){",
            "async function factory({apiKey}){if(!apiKey)return {};",
        )
    transformed = patchset.apply(graph)
    assert "const _ccMultiProviderSDK" not in transformed
    assert _execute(tmp_path, finalize_module_runtime(transformed), "entry.mjs") == {
        "early": 1048576,
        "same": True,
        "model": "kimi-k3",
        "native": True,
    }


def test_unregistered_sources_unchanged() -> None:
    assert finalize_module_runtime("function native(){}") == "function native(){}"
