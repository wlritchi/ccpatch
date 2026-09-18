"""Mach-O container surgery for Bun standalone binaries (macOS), via ``lief``.

Bun embeds its data blob in the ``__bun`` section of the ``__BUN`` segment,
wrapped as ``[u64 length][blob]``. Resizing it requires extending the segment
(which shifts ``__LINKEDIT`` and every load command that references it by
absolute offset) and re-signing ad-hoc -- work that is not worth hand-rolling,
so we delegate to ``lief``. Ported from tweakcc's ``repackMachO``.

``lief`` is imported lazily so the Linux ELF path needs no native dependency;
on macOS it is provided by ``python3Packages.lief`` in the Nix build.

NOTE: the :meth:`write_blob` path is **unvalidated on macOS** in this repo (no
Mac binary available where it was authored). Reading is straightforward; the
resize + re-sign needs a validation pass on a real arm64/x86_64 Claude binary
before being relied upon. The ELF path (:mod:`ccpatch.elf`) is validated.
"""

from __future__ import annotations

import math
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import lief  # type: ignore  # macOS-only; resolved by python3Packages.lief (Nix)

# Mach-O 64-bit magic, both endiannesses, plus fat magics we explicitly reject.
_MACHO_THIN_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")
_MACHO_FAT_MAGICS = (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")

# macOS page sizes: 16 KiB on Apple silicon, 4 KiB on Intel.
_PAGE_ARM64 = 16384
_PAGE_X86_64 = 4096


class MachoLayoutError(RuntimeError):
    """The Mach-O binary lacks the expected ``__BUN``/``__bun`` Bun layout."""


@dataclass
class MachoContainer:
    """A Bun Mach-O binary backed by a parsed ``lief`` object."""

    _binary: lief.MachO.Binary
    _section_header_size: int  # 4 (Bun < 1.3.4) or 8 (>= 1.3.4)

    @classmethod
    def sniff(cls, data: bytes) -> bool:
        head = data[:4]
        if head in _MACHO_FAT_MAGICS:
            # Anthropic ships thin per-arch binaries; a fat binary is unexpected.
            raise MachoLayoutError(
                "fat Mach-O not supported; expected a thin per-arch binary"
            )
        return head in _MACHO_THIN_MAGICS

    @classmethod
    def from_bytes(cls, data: bytes) -> MachoContainer:
        import lief  # type: ignore  # macOS-only; resolved by python3Packages.lief (Nix)

        parsed = lief.MachO.parse(list(data))
        binary = parsed.at(0) if hasattr(parsed, "at") else parsed
        section = cls._bun_section(binary)
        header_size = cls._detect_header_size(bytes(section.content))
        return cls(_binary=binary, _section_header_size=header_size)

    @staticmethod
    def _bun_section(binary: lief.MachO.Binary) -> lief.MachO.Section:
        segment = binary.get_segment("__BUN")
        if segment is None:
            raise MachoLayoutError("__BUN segment not found")
        for section in segment.sections:
            if section.name == "__bun":
                return section
        raise MachoLayoutError("__bun section not found")

    @staticmethod
    def _detect_header_size(content: bytes) -> int:
        if len(content) >= 8 and 8 + struct.unpack_from("<Q", content, 0)[0] <= len(
            content
        ):
            return 8
        return 4

    def read_blob(self) -> bytes:
        content = bytes(self._bun_section(self._binary).content)
        if self._section_header_size == 8:
            length = struct.unpack_from("<Q", content, 0)[0]
        else:
            length = struct.unpack_from("<I", content, 0)[0]
        return content[self._section_header_size : self._section_header_size + length]

    def write_blob(self, new_blob: bytes) -> bytes:
        """Repack and return new Mach-O bytes (UNVALIDATED on macOS; see module)."""
        import lief  # type: ignore  # macOS-only; resolved by python3Packages.lief (Nix)

        binary = self._binary
        if binary.has_code_signature:
            binary.remove_signature()

        segment = binary.get_segment("__BUN")
        section = self._bun_section(binary)
        new_content = self._wrap_section(new_blob)

        size_diff = len(new_content) - section.size
        if size_diff > 0:
            is_arm64 = binary.header.cpu_type == lief.MachO.Header.CPU_TYPE.ARM64
            page = _PAGE_ARM64 if is_arm64 else _PAGE_X86_64
            aligned = math.ceil(size_diff / page) * page
            if not binary.extend_segment(segment, aligned):
                raise MachoLayoutError("failed to extend __BUN segment")

        section.content = list(new_content)
        section.size = len(new_content)

        out = Path(_tmp_path())
        binary.write(str(out))
        try:
            self._codesign_adhoc(out)
            return out.read_bytes()
        finally:
            out.unlink(missing_ok=True)

    def _wrap_section(self, blob: bytes) -> bytes:
        if self._section_header_size == 8:
            return struct.pack("<Q", len(blob)) + blob
        return struct.pack("<I", len(blob)) + blob

    @staticmethod
    def _codesign_adhoc(path: Path) -> None:
        result = subprocess.run(  # noqa: S603  # trusted absolute path
            ["/usr/bin/codesign", "--force", "--sign", "-", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MachoLayoutError(
                f"ad-hoc codesign failed: {(result.stderr or result.stdout).strip()}"
            )


def _tmp_path() -> str:
    import tempfile

    fd, name = tempfile.mkstemp(suffix=".macho")
    import os

    os.close(fd)
    return name
