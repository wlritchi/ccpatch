"""ELF container surgery for Bun standalone binaries (Linux), no dependencies.

Bun >= ~1.3 embeds its data blob in a ``.bun`` ELF section wrapped as
``[u64 length][blob]``. This module reads that blob and writes back a
(possibly resized) blob by growing/shrinking ``.bun`` in place.

The repack only supports the favorable-but-universal-in-practice layout where
``.bun`` is the **last loadable section** (highest file offset among
``SHF_ALLOC`` sections) inside the topmost ``PT_LOAD``. In that case a resize
needs no relocation: ``.bun``'s start offset/address are unchanged (so Bun's
embedded ``BUN_COMPILED`` pointer stays valid), and we only

* rewrite ``.bun``'s ``sh_size``,
* grow/shrink the writable ``PT_LOAD``'s ``p_filesz``/``p_memsz``,
* shift trailing (non-alloc) section offsets and ``e_shoff`` by the delta.

The kernel ignores section headers at exec, so the section-header bookkeeping
is for tools only; correctness for *running* depends solely on the program
header edits. :func:`ElfContainer.from_bytes` asserts the layout precondition
and raises :class:`ElfLayoutError` if a future binary violates it.

Validated against Claude Code 2.1.170 (linux-x64).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# ELF64 header field offsets.
_E_PHOFF = 0x20
_E_SHOFF = 0x28
_E_PHENTSIZE = 0x36
_E_PHNUM = 0x38
_E_SHENTSIZE = 0x3A
_E_SHNUM = 0x3C
_E_SHSTRNDX = 0x3E

# Elf64_Shdr field offsets.
_SH_OFFSET = 0x18
_SH_SIZE = 0x20

# Elf64_Phdr field offsets.
_PH_OFFSET = 0x08
_PH_VADDR = 0x10
_PH_FILESZ = 0x20
_PH_MEMSZ = 0x28

_PT_LOAD = 1
_SHF_ALLOC = 0x2


class ElfLayoutError(RuntimeError):
    """The binary's ``.bun`` section is not in the supported (last-load) layout."""


@dataclass(frozen=True)
class _Section:
    index: int
    hdr_offset: int  # position of this section's header entry in the file
    name: str
    flags: int
    addr: int
    offset: int
    size: int


def _u16(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u64(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def _read_sections(data: bytes) -> list[_Section]:
    shoff = _u64(data, _E_SHOFF)
    shentsize = _u16(data, _E_SHENTSIZE)
    shnum = _u16(data, _E_SHNUM)
    shstrndx = _u16(data, _E_SHSTRNDX)
    strtab = _u64(data, shoff + shstrndx * shentsize + _SH_OFFSET)
    out: list[_Section] = []
    for i in range(shnum):
        h = shoff + i * shentsize
        name_off = struct.unpack_from("<I", data, h)[0]
        end = data.index(b"\x00", strtab + name_off)
        out.append(
            _Section(
                index=i,
                hdr_offset=h,
                name=data[strtab + name_off : end].decode("ascii", "replace"),
                flags=_u64(data, h + 8),
                addr=_u64(data, h + 0x10),
                offset=_u64(data, h + _SH_OFFSET),
                size=_u64(data, h + _SH_SIZE),
            )
        )
    return out


@dataclass(frozen=True)
class ElfContainer:
    """A parsed Bun ELF, ready to read or rewrite its ``.bun`` blob."""

    data: bytes
    _bun_hdr_offset: int
    _bun_offset: int
    _bun_size: int
    _load_phdr_offset: int
    _load_filesz: int
    _load_memsz: int

    @classmethod
    def sniff(cls, data: bytes) -> bool:
        return data[:4] == b"\x7fELF" and len(data) > 0x40 and data[4] == 2

    @classmethod
    def from_bytes(cls, data: bytes) -> ElfContainer:
        if not cls.sniff(data):
            raise ElfLayoutError("not an ELF64 binary")
        sections = _read_sections(data)
        bun = next((s for s in sections if s.name == ".bun"), None)
        if bun is None:
            raise ElfLayoutError("no .bun section")

        # Precondition: .bun is the last loadable section in the file.
        later_alloc = [
            s for s in sections if s.offset > bun.offset and (s.flags & _SHF_ALLOC)
        ]
        if later_alloc:
            raise ElfLayoutError(
                ".bun is not the last loadable section; alloc sections follow: "
                + ", ".join(s.name for s in later_alloc)
            )

        load = cls._find_containing_load(data, bun.addr)
        return cls(
            data=data,
            _bun_hdr_offset=bun.hdr_offset,
            _bun_offset=bun.offset,
            _bun_size=bun.size,
            _load_phdr_offset=load[0],
            _load_filesz=load[1],
            _load_memsz=load[2],
        )

    @staticmethod
    def _find_containing_load(data: bytes, bun_addr: int) -> tuple[int, int, int]:
        """Find the (topmost) PT_LOAD containing ``.bun``; flags are irrelevant.

        Growing ``.bun`` in place enlarges this segment upward, so it must be the
        highest-addressed load (nothing mapped above it to collide with).
        """
        phoff = _u64(data, _E_PHOFF)
        phentsize = _u16(data, _E_PHENTSIZE)
        phnum = _u16(data, _E_PHNUM)
        loads: list[int] = []
        found: tuple[int, int, int] | None = None
        for i in range(phnum):
            h = phoff + i * phentsize
            if struct.unpack_from("<I", data, h)[0] != _PT_LOAD:
                continue
            p_vaddr = _u64(data, h + _PH_VADDR)
            p_memsz = _u64(data, h + _PH_MEMSZ)
            loads.append(p_vaddr)
            if p_vaddr <= bun_addr < p_vaddr + p_memsz:
                found = (h, _u64(data, h + _PH_FILESZ), p_memsz)
        if found is None:
            raise ElfLayoutError("no PT_LOAD contains .bun")
        if max(loads) != _u64(data, found[0] + _PH_VADDR):
            raise ElfLayoutError(".bun's PT_LOAD is not the topmost load segment")
        return found

    def read_blob(self) -> bytes:
        """Return the raw Bun data blob (section content minus the u64 header)."""
        blob_len = _u64(self.data, self._bun_offset)
        start = self._bun_offset + 8
        return self.data[start : start + blob_len]

    def write_blob(self, new_blob: bytes) -> bytes:
        """Return new file bytes with ``.bun`` replaced by ``new_blob``."""
        new_section = struct.pack("<Q", len(new_blob)) + new_blob
        delta = len(new_section) - self._bun_size

        new = bytearray(self.data[: self._bun_offset])
        new += new_section
        new += self.data[self._bun_offset + self._bun_size :]

        # Section-header table sits after .bun, so it moved by delta.
        old_shoff = _u64(self.data, _E_SHOFF)
        new_shoff = old_shoff + delta
        struct.pack_into("<Q", new, _E_SHOFF, new_shoff)

        shentsize = _u16(self.data, _E_SHENTSIZE)
        shnum = _u16(self.data, _E_SHNUM)
        for i in range(shnum):
            h = new_shoff + i * shentsize
            off = _u64(new, h + _SH_OFFSET)
            if off > self._bun_offset:
                struct.pack_into("<Q", new, h + _SH_OFFSET, off + delta)
        # .bun's own header entry moved by delta; set its new size.
        struct.pack_into(
            "<Q", new, self._bun_hdr_offset + delta + _SH_SIZE, len(new_section)
        )

        # Grow/shrink the containing load segment; shift any phdr past .bun.
        struct.pack_into(
            "<Q", new, self._load_phdr_offset + _PH_FILESZ, self._load_filesz + delta
        )
        struct.pack_into(
            "<Q", new, self._load_phdr_offset + _PH_MEMSZ, self._load_memsz + delta
        )
        phoff = _u64(self.data, _E_PHOFF)
        phentsize = _u16(self.data, _E_PHENTSIZE)
        phnum = _u16(self.data, _E_PHNUM)
        for i in range(phnum):
            h = phoff + i * phentsize
            p_off = _u64(new, h + _PH_OFFSET)
            if p_off > self._bun_offset:
                struct.pack_into("<Q", new, h + _PH_OFFSET, p_off + delta)

        return bytes(new)
