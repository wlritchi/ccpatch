"""ccpatch: reproducible patching of Claude Code's Bun standalone binary.

Extract the embedded ``cli.js`` source from a Bun single-file executable, apply
length-free JavaScript transforms, rebuild the Bun data blob, and repack the
container (pure-Python ELF on Linux; ``lief``-backed Mach-O on macOS). Designed
to run inside a Nix build phase for reproducible, immutable patched binaries.
"""

from __future__ import annotations

from ccpatch.bunfmt import BunBlob, BunModule, parse_blob, rebuild_blob
from ccpatch.container import Container, load_container

__all__ = [
    "BunBlob",
    "BunModule",
    "Container",
    "load_container",
    "parse_blob",
    "rebuild_blob",
]
