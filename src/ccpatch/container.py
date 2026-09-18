"""Container abstraction: read/write the Bun data blob across binary formats.

Linux ELF is handled in pure Python (:mod:`ccpatch.elf`). macOS Mach-O
is handled via ``lief`` (:mod:`ccpatch.macho`), imported lazily so the
common Linux path needs no native dependency.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Container(Protocol):
    """A binary whose embedded Bun data blob can be read and replaced."""

    def read_blob(self) -> bytes: ...

    def write_blob(self, new_blob: bytes) -> bytes: ...


class UnknownContainerError(RuntimeError):
    """The bytes are neither a supported ELF nor Mach-O Bun binary."""


def load_container(data: bytes) -> Container:
    """Detect the binary format and return the matching container."""
    from ccpatch.elf import ElfContainer

    if ElfContainer.sniff(data):
        return ElfContainer.from_bytes(data)

    from ccpatch.macho import MachoContainer

    if MachoContainer.sniff(data):
        return MachoContainer.from_bytes(data)

    raise UnknownContainerError("unrecognized binary format (not ELF64 or Mach-O)")
