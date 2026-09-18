"""Bun standalone-executable data blob: parse and rebuild.

A Bun single-file executable embeds its compiled module graph in a data blob
laid out as ``[strings...][module table][compileExecArgv][OFFSETS][TRAILER]``.
Each module references its strings (source ``contents``, ``bytecode``, etc.) by
``(u32 offset, u32 length)`` pointers into the blob.

This module parses that blob into :class:`BunBlob`, lets a caller swap a
module's ``contents`` (or zero its ``bytecode``), and rebuilds a fresh,
self-consistent blob. The rebuild is canonical (tightly packed, ``byteCount``
set to the offsets-struct position) -- byte-for-byte different from Bun's
original layout but valid for Bun's reader. Ported from tweakcc's
``nativeInstallation.ts`` and validated against Claude Code 2.1.170.

Length-free: the rebuilt blob may be any size; container repack handles the
resulting section/segment resize.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, field, replace

BUN_TRAILER = b"\n---- Bun! ----\n"

SIZEOF_OFFSETS = 32
SIZEOF_STRING_POINTER = 8
# Module struct = N string pointers + 4 trailing u8s (encoding/loader/format/side).
# Old (pre-ESM-bytecode, Bun < ~1.3.7): 4 pointers = 36 bytes.
# New (ESM bytecode, Bun >= ~1.3.7):    6 pointers = 52 bytes.
SIZEOF_MODULE_OLD = 4 * SIZEOF_STRING_POINTER + 4
SIZEOF_MODULE_NEW = 6 * SIZEOF_STRING_POINTER + 4
_STRINGS_OLD = 4
_STRINGS_NEW = 6
_TAIL = 4


class BunFormatError(ValueError):
    """The byte stream does not match the expected Bun blob layout."""


@dataclass(frozen=True)
class BunModule:
    """One module in the graph. Empty ``bytes`` means an absent string slot.

    ``module_info`` and ``bytecode_origin_path`` exist only in the new format;
    they are ignored when the blob's ``module_struct_size`` is the old size.
    """

    name: bytes
    contents: bytes
    sourcemap: bytes
    bytecode: bytes
    module_info: bytes
    bytecode_origin_path: bytes
    tail: bytes  # 4 bytes: encoding, loader, moduleFormat, side

    def is_entrypoint(self) -> bool:
        """True for the native CLI entry module (``cli.js`` / ``claude``)."""
        return (
            self.name.endswith(b"/cli.js")
            or self.name.endswith(b"/claude")
            or self.name.endswith(b"/cli")
            or self.name in (b"cli.js", b"claude", b"cli")
        )


@dataclass(frozen=True)
class BunBlob:
    entry_point_id: int
    flags: int
    compile_exec_argv: bytes
    modules: tuple[BunModule, ...]
    module_struct_size: int  # SIZEOF_MODULE_OLD or SIZEOF_MODULE_NEW
    original: bytes = field(default=b"", repr=False, compare=False)
    extra_records: bytes = field(default=b"", repr=False)

    @property
    def is_new_format(self) -> bool:
        return self.module_struct_size == SIZEOF_MODULE_NEW

    def map_modules(self, fn: Callable[[BunModule], BunModule | None]) -> BunBlob:
        """Return a copy with each module replaced by ``fn(module)`` (or kept)."""
        new = tuple(fn(m) or m for m in self.modules)
        return replace(self, modules=new)


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _string_at(blob: bytes, mlist: bytes, ptr_off: int) -> bytes:
    """Resolve a ``(u32 offset, u32 length)`` pointer (blob-relative)."""
    off, length = struct.unpack_from("<II", mlist, ptr_off)
    return blob[off : off + length]


def _detect_struct_size(modules_list_len: int) -> int:
    fits_new = modules_list_len % SIZEOF_MODULE_NEW == 0
    fits_old = modules_list_len % SIZEOF_MODULE_OLD == 0
    if fits_old and not fits_new:
        return SIZEOF_MODULE_OLD
    # Ambiguous or new-only: prefer new (current Bun).
    return SIZEOF_MODULE_NEW


def parse_blob(blob: bytes) -> BunBlob:
    """Parse a raw Bun data blob (``[data][OFFSETS][TRAILER]``)."""
    if len(blob) < SIZEOF_OFFSETS + len(BUN_TRAILER):
        raise BunFormatError("blob too small for offsets + trailer")
    if blob[-len(BUN_TRAILER) :] != BUN_TRAILER:
        raise BunFormatError("missing Bun trailer")

    off = len(blob) - SIZEOF_OFFSETS - len(BUN_TRAILER)
    # OFFSETS: u64 byteCount, modulesPtr(u32,u32), u32 entryPointId,
    #          compileExecArgvPtr(u32,u32), u32 flags
    modules_off, modules_len = struct.unpack_from("<II", blob, off + 8)
    entry_point_id = _u32(blob, off + 16)
    cea_off, cea_len = struct.unpack_from("<II", blob, off + 20)
    flags = _u32(blob, off + 28)

    struct_size = _detect_struct_size(modules_len)
    n_strings = _STRINGS_NEW if struct_size == SIZEOF_MODULE_NEW else _STRINGS_OLD
    mlist = blob[modules_off : modules_off + modules_len]

    modules: list[BunModule] = []
    for base in range(0, len(mlist) - struct_size + 1, struct_size):
        strings = [
            _string_at(blob, mlist, base + k * SIZEOF_STRING_POINTER)
            for k in range(n_strings)
        ]
        tail = mlist[base + n_strings * SIZEOF_STRING_POINTER : base + struct_size]
        modules.append(
            BunModule(
                name=strings[0],
                contents=strings[1],
                sourcemap=strings[2],
                bytecode=strings[3],
                module_info=strings[4] if n_strings == _STRINGS_NEW else b"",
                bytecode_origin_path=strings[5] if n_strings == _STRINGS_NEW else b"",
                tail=tail,
            )
        )

    return BunBlob(
        entry_point_id=entry_point_id,
        flags=flags,
        compile_exec_argv=blob[cea_off : cea_off + cea_len],
        modules=tuple(modules),
        module_struct_size=struct_size,
        original=blob,
        extra_records=blob[modules_off + modules_len : cea_off]
        if flags & ~0xF
        else b"",
    )


def _source_only(blob: BunBlob) -> BunBlob:
    """Remove graph-wide caches, but keep the compiled runtime policy."""
    records = blob.extra_records
    pos = 0
    if blob.flags & (1 << 5):
        pos += 4 * len(blob.modules)
    if blob.flags & (1 << 6):
        count = _u32(records, pos)
        pos += 4 + 12 * count
    if blob.flags & (1 << 7):
        pos += 8
    if blob.flags & (1 << 8):
        pos += 4
    if blob.flags & (1 << 9):
        pos += 8
    if blob.flags & (1 << 11):
        count = _u32(records, pos + 8)
        pos += 12 + 4 * count
    runtime = records[pos : pos + 8] if blob.flags & (1 << 12) else b""
    if blob.flags & ~0x1FFF:
        raise BunFormatError("unsupported Bun graph flags")
    return replace(
        blob,
        flags=blob.flags & (0xF | (1 << 10) | (1 << 12)),
        modules=tuple(
            replace(m, bytecode=b"", bytecode_origin_path=b"", module_info=b"")
            for m in blob.modules
        ),
        extra_records=runtime,
    )


def rebuild_blob(blob: BunBlob) -> bytes:
    """Serialize a graph; invalidate shared caches when its modules change.

    Unchanged parsed graphs retain their original layout and caches. Rebuilt
    extended graphs use source because their hashes, shared tables, and prelinked
    metadata depend on the original source and module layout.
    """
    if blob.original and blob == parse_blob(blob.original):
        return blob.original
    if blob.flags & ~0xF and blob.extra_records:
        blob = _source_only(blob)
    new_format = blob.is_new_format
    n_strings = _STRINGS_NEW if new_format else _STRINGS_OLD

    # Phase 1: collect every string in module order.
    strings: list[bytes] = []
    for m in blob.modules:
        strings.extend([m.name, m.contents, m.sourcemap, m.bytecode])
        if new_format:
            strings.extend([m.module_info, m.bytecode_origin_path])

    # Phase 2: assign offsets (each string gets a trailing NUL separator).
    str_ptrs: list[tuple[int, int]] = []
    cur = 0
    for i, s in enumerate(strings):
        if i % n_strings == 3 and s:
            cur += (120 - cur) % 128
        elif i % n_strings in (1, 4):
            cur += (-cur) % 4
        str_ptrs.append((cur, len(s)))
        cur += len(s) + 1
    modules_list_off = cur
    modules_list_size = len(blob.modules) * blob.module_struct_size
    cur += modules_list_size
    records_off = cur
    cur += len(blob.extra_records)
    cea_off = cur
    cur += len(blob.compile_exec_argv) + 1
    offsets_off = cur
    cur += SIZEOF_OFFSETS
    trailer_off = cur
    cur += len(BUN_TRAILER)

    buf = bytearray(cur)

    for (off, length), s in zip(str_ptrs, strings, strict=True):
        buf[off : off + length] = s
        buf[off + length] = 0  # NUL terminator

    for i in range(len(blob.modules)):
        mo = modules_list_off + i * blob.module_struct_size
        for k in range(n_strings):
            p_off, p_len = str_ptrs[i * n_strings + k]
            struct.pack_into("<II", buf, mo + k * SIZEOF_STRING_POINTER, p_off, p_len)
        tail_at = mo + n_strings * SIZEOF_STRING_POINTER
        buf[tail_at : tail_at + _TAIL] = blob.modules[i].tail

    buf[records_off:cea_off] = blob.extra_records
    cea = blob.compile_exec_argv
    buf[cea_off : cea_off + len(cea)] = cea
    buf[cea_off + len(cea)] = 0

    # OFFSETS: byteCount is the offsets-struct position (per Bun/tweakcc).
    struct.pack_into("<Q", buf, offsets_off, offsets_off)
    struct.pack_into("<II", buf, offsets_off + 8, modules_list_off, modules_list_size)
    struct.pack_into("<I", buf, offsets_off + 16, blob.entry_point_id)
    struct.pack_into("<II", buf, offsets_off + 20, cea_off, len(cea))
    struct.pack_into("<I", buf, offsets_off + 28, blob.flags)

    buf[trailer_off : trailer_off + len(BUN_TRAILER)] = BUN_TRAILER
    return bytes(buf)
