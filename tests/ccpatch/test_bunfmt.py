"""Round-trip and edit tests for the Bun blob (de)serializer."""

from __future__ import annotations

import struct
from dataclasses import replace

import pytest

from ccpatch.bunfmt import (
    SIZEOF_MODULE_NEW,
    SIZEOF_MODULE_OLD,
    BunBlob,
    BunFormatError,
    BunModule,
    parse_blob,
    rebuild_blob,
)
from ccpatch.cli import (
    ApplyError,
    _graph_source,
    _source_modules,
    _split_graph_source,
)


def _module(
    name: bytes, contents: bytes, *, new: bool, bytecode: bytes = b""
) -> BunModule:
    return BunModule(
        name=name,
        contents=contents,
        sourcemap=b"",
        bytecode=bytecode,
        module_info=b"\x01\x02" if new else b"",
        bytecode_origin_path=name if new and bytecode else b"",
        tail=b"\x00\x01\x02\x03",
    )


def _blob(*, new: bool) -> BunBlob:
    size = SIZEOF_MODULE_NEW if new else SIZEOF_MODULE_OLD
    return BunBlob(
        entry_point_id=0,
        flags=0x2A,
        compile_exec_argv=b"--smol",
        modules=(
            _module(
                b"/$bunfs/root/cli.js", b"console.log(1)", new=new, bytecode=b"\xde\xad"
            ),
            _module(b"/$bunfs/root/helper.js", b"export const x=2", new=new),
        ),
        module_struct_size=size,
    )


@pytest.mark.parametrize("new", [True, False])
def test_round_trip_is_identity(new: bool) -> None:
    blob = _blob(new=new)
    assert parse_blob(rebuild_blob(blob)) == blob


@pytest.mark.parametrize("new", [True, False])
def test_rebuilt_blob_is_canonical_and_stable(new: bool) -> None:
    once = rebuild_blob(_blob(new=new))
    twice = rebuild_blob(parse_blob(once))
    assert once == twice  # rebuild is a fixed point


def test_entrypoint_detection() -> None:
    blob = _blob(new=True)
    entry = [m for m in blob.modules if m.is_entrypoint()]
    assert [m.name for m in entry] == [b"/$bunfs/root/cli.js"]


def test_swap_contents_changes_length_and_survives_round_trip() -> None:
    blob = _blob(new=True)

    def patch(m: BunModule) -> BunModule | None:
        if m.is_entrypoint():
            return BunModule(**{**m.__dict__, "contents": m.contents + b" /*patched*/"})
        return None

    patched = parse_blob(rebuild_blob(blob.map_modules(patch)))
    entry = next(m for m in patched.modules if m.is_entrypoint())
    assert entry.contents.endswith(b" /*patched*/")


def test_zeroing_bytecode_round_trips() -> None:
    blob = _blob(new=True)

    def drop_bytecode(m: BunModule) -> BunModule | None:
        return BunModule(**{**m.__dict__, "bytecode": b"", "bytecode_origin_path": b""})

    rebuilt = parse_blob(rebuild_blob(blob.map_modules(drop_bytecode)))
    assert all(m.bytecode == b"" for m in rebuilt.modules)


def test_split_graph_preserves_modules_and_assets() -> None:
    blob = _blob(new=True)
    executable = b"\x01\x01\x01\x00"
    entry = replace(blob.modules[0], name=b"/$bunfs/root/cli", tail=executable)
    helper = replace(blob.modules[1], tail=executable)
    asset = replace(helper, name=b"mermaid.min.js", tail=b"\x00\x05\x00\x01")
    blob = replace(blob, entry_point_id=1, modules=(helper, entry, asset))
    modules = _source_modules(blob)
    assert modules == (helper, entry)
    assert entry.is_entrypoint()
    source = _graph_source(modules)
    assert _split_graph_source(source, modules) == {m.name: m.contents for m in modules}
    patched = _split_graph_source(
        source.replace("console.log(1)", "console.log(22)"), modules
    )
    assert patched[entry.name] == b"console.log(22)"
    assert patched[helper.name] == helper.contents
    with pytest.raises(ApplyError, match="boundary"):
        _split_graph_source("extra" + source, modules)


def test_extended_graph_drops_caches_but_preserves_runtime_policy() -> None:
    blob = _blob(new=True)
    runtime = struct.pack("<II", 1, 0x40000000)
    records = (
        b"\0" * (4 * len(blob.modules))
        + struct.pack("<I", 0)
        + b"\0" * 8
        + struct.pack("<I", 1)
        + b"\0" * 8
        + struct.pack("<III", 0, 0, 0)
        + runtime
    )
    blob = replace(blob, flags=0x1BFF, extra_records=records)
    rebuilt = parse_blob(rebuild_blob(blob))
    assert rebuilt.flags == 0x100F
    assert rebuilt.extra_records == runtime
    assert all(not m.bytecode and not m.module_info for m in rebuilt.modules)
    assert rebuilt.entry_point_id == blob.entry_point_id
    assert rebuilt.compile_exec_argv == blob.compile_exec_argv
    assert rebuild_blob(rebuilt) == rebuilt.original


def test_missing_trailer_rejected() -> None:
    with pytest.raises(BunFormatError):
        parse_blob(b"not a bun blob")
