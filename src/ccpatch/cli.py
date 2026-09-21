"""``ccpatch`` command line: reproducibly patch a Claude Code Bun binary.

Pipeline: read binary -> extract ``cli.js`` source from the Bun blob -> apply
length-free JS patches -> rebuild the blob (optionally zeroing bytecode) ->
repack the container -> verify (re-extract structurally + run ``--version``) ->
write output. Any failure exits non-zero so a Nix build fails loudly rather than
producing a binary that looks patched but isn't.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from ccpatch.bunfmt import BunBlob, BunModule, parse_blob, rebuild_blob
from ccpatch.container import load_container
from ccpatch.module_runtime import ModuleRuntimeError, finalize_module_runtime
from ccpatch.patches import (
    _MULTI_PROVIDER_HELPER,
    PatchError,
    default_patch_sets,
    parse_version,
)
from ccpatch.transcripts import MigrationError, migrate_file, transcript_paths

_CLAUDE_VERSION_RE = re.compile(r"\(Claude Code\)")


class ApplyError(RuntimeError):
    """The apply pipeline failed (patch, repack, or verification)."""


@dataclass(frozen=True)
class ApplyResult:
    out_path: Path
    version: str | None
    bytecode_zeroed: bool
    source_before: int
    source_after: int


def _decode(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")


def _encode(s: str) -> bytes:
    return s.encode("utf-8", "surrogateescape")


def _patch_source(source: str, version: str | None) -> str:
    parsed = parse_version(version) if version else None
    patch_sets = default_patch_sets(parsed)
    for patch_set in patch_sets:
        if patch_set.applies_to(parsed):
            source = patch_set.apply(source)
    return finalize_module_runtime(source)


def _source_modules(blob: BunBlob) -> tuple[BunModule, ...]:
    if not 0 <= blob.entry_point_id < len(blob.modules):
        raise ApplyError("invalid Bun entrypoint module index")
    entry = blob.modules[blob.entry_point_id]
    if entry.tail[2] != 1:
        return (entry,)
    return tuple(m for m in blob.modules if m.tail[1:4] == b"\x01\x01\x00")


def _module_marker(module: BunModule) -> str:
    return f"\n/* ccpatch-module:{module.name.hex()} */\n"


def _graph_source(modules: tuple[BunModule, ...]) -> str:
    if len(modules) == 1:
        return _decode(modules[0].contents)
    return "".join(_module_marker(m) + _decode(m.contents) for m in modules)


def _split_graph_source(
    source: str, modules: tuple[BunModule, ...]
) -> dict[bytes, bytes]:
    if len(modules) == 1:
        return {modules[0].name: _encode(source)}
    boundaries = list(re.finditer(r"\n/\* ccpatch-module:([0-9a-f]+) \*/\n", source))
    if (
        len(boundaries) != len(modules)
        or boundaries[0].start() != 0
        or any(
            match.group(0) != _module_marker(module)
            for match, module in zip(boundaries, modules, strict=True)
        )
    ):
        raise ApplyError("patch changed a Bun module boundary")
    ends = [match.start() for match in boundaries[1:]] + [len(source)]
    return {
        module.name: _encode(source[match.end() : end])
        for module, match, end in zip(modules, boundaries, ends, strict=True)
    }


def apply_patches(
    data: bytes,
    *,
    version: str | None,
    zero_bytecode: bool,
) -> bytes:
    """Return new binary bytes with patched ``cli.js`` source repacked in."""
    container = load_container(data)
    blob = parse_blob(container.read_blob())

    modules = _source_modules(blob)
    patched_source = _patch_source(_graph_source(modules), version)
    sources = _split_graph_source(patched_source, modules)

    def transform(module: BunModule) -> BunModule | None:
        if module.name not in sources:
            return None
        contents = sources[module.name]
        updates: dict[str, bytes] = {"contents": contents}
        if zero_bytecode or contents != module.contents:
            updates["bytecode"] = b""
            updates["bytecode_origin_path"] = b""
            updates["module_info"] = b""
        return replace(module, **updates)

    new_blob = rebuild_blob(blob.map_modules(transform))
    return container.write_blob(new_blob)


def _entry_source(data: bytes) -> str:
    blob = parse_blob(load_container(data).read_blob())
    return _graph_source(_source_modules(blob))


def _run_smoke_command(path: Path, argument: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603  # trusted constructed path
            [str(path), argument],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "DISABLE_AUTOUPDATER": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ApplyError(f"patched binary failed to run {argument}: {exc}") from exc


def _smoke_test(path: Path) -> str:
    """Run startup-only CLI commands; return the reported version or raise."""
    version_result = _run_smoke_command(path, "--version")
    line = (
        (version_result.stdout or "").splitlines()[0] if version_result.stdout else ""
    )
    if version_result.returncode != 0 or not _CLAUDE_VERSION_RE.search(line):
        raise ApplyError(
            "smoke test failed: binary did not report a Claude version "
            f"(exit {version_result.returncode}, output {line!r}). Possible Bun fallback."
        )

    help_result = _run_smoke_command(path, "--help")
    if help_result.returncode != 0 or "Usage:" not in help_result.stdout:
        detail = (help_result.stderr or help_result.stdout).strip().splitlines()
        raise ApplyError(
            "smoke test failed: binary did not initialize CLI help "
            f"(exit {help_result.returncode}, output {detail[-1] if detail else ''!r})"
        )
    return line


def run_apply(
    in_path: Path,
    out_path: Path,
    *,
    version: str | None,
    zero_bytecode: bool,
    smoke: bool,
) -> ApplyResult:
    data = in_path.read_bytes()
    before = len(_entry_source(data))
    new_data = apply_patches(data, version=version, zero_bytecode=zero_bytecode)

    # Structural verify: the rewritten binary must re-extract and contain our edit.
    after_source = _entry_source(new_data)
    if "isTranscriptMode:true,verbose:true" not in after_source:
        raise ApplyError("structural verify failed: patched source not found in output")

    out_path.write_bytes(new_data)
    out_path.chmod(out_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    reported = _smoke_test(out_path) if smoke else None
    return ApplyResult(
        out_path=out_path,
        version=reported,
        bytecode_zeroed=zero_bytecode,
        source_before=before,
        source_after=len(after_source),
    )


def _cmd_apply(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else Path(args.input)
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "claude"
        try:
            result = run_apply(
                Path(args.input),
                staged,
                version=args.version,
                zero_bytecode=args.zero_bytecode,
                smoke=not args.no_smoke,
            )
        except (ApplyError, PatchError, ModuleRuntimeError) as exc:
            print(f"ccpatch: {exc}", file=sys.stderr)
            return 1
        out.write_bytes(staged.read_bytes())
        out.chmod(staged.stat().st_mode)
    delta = result.source_after - result.source_before
    print(
        f"ccpatch: patched {out} "
        f"(source {result.source_before} -> {result.source_after} bytes, {delta:+d}; "
        f"bytecode {'zeroed' if result.bytecode_zeroed else 'retained'}; "
        f"{result.version or 'no smoke test'})"
    )
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    source = _entry_source(Path(args.input).read_bytes())
    Path(args.out).write_text(source, encoding="utf-8", errors="surrogateescape")
    print(f"ccpatch: wrote {len(source)} bytes of cli.js to {args.out}")
    return 0


def _cmd_generate_multi_provider_helper(args: argparse.Namespace) -> int:
    Path(args.out).write_text(_MULTI_PROVIDER_HELPER, encoding="utf-8")
    return 0


def _cmd_migrate_tool_ids(args: argparse.Namespace) -> int:
    root = Path(args.projects_dir).expanduser()
    try:
        paths = transcript_paths(root)
    except (OSError, MigrationError) as exc:
        print(f"ccpatch: {exc}", file=sys.stderr)
        return 1
    affected = replacements = failures = 0
    for path in paths:
        try:
            result = migrate_file(path, apply=args.apply)
        except (OSError, MigrationError) as exc:
            print(f"ccpatch: {path}: {exc}", file=sys.stderr)
            failures += 1
            continue
        if result.replacements:
            affected += 1
            replacements += result.replacements
            action = "updated" if args.apply else "would update"
            print(f"{action}: {path} ({result.replacements} ID fields)")
            if result.backup:
                print(f"  backup: {result.backup}")
    print(
        f"ccpatch: {'applied' if args.apply else 'preview'}: "
        f"{len(paths)} transcripts scanned, {affected} affected, "
        f"{replacements} ID fields, {failures} errors"
    )
    if not args.apply:
        print("No files changed. Stop Claude sessions before rerunning with --apply.")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccpatch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ap = sub.add_parser("apply", help="patch a Claude Code binary")
    ap.add_argument("input", help="path to the Bun standalone binary")
    ap.add_argument("-o", "--out", help="output path (default: overwrite input)")
    ap.add_argument("--version", help="Claude version, e.g. 2.1.170 (gates patches)")
    ap.add_argument(
        "--zero-bytecode",
        action="store_true",
        help="drop the entrypoint bytecode (forces recompile-from-source; shrinks binary)",
    )
    ap.add_argument(
        "--no-smoke", action="store_true", help="skip the --version smoke test"
    )
    ap.set_defaults(func=_cmd_apply)

    ex = sub.add_parser("extract", help="dump the embedded cli.js source")
    ex.add_argument("input", help="path to the Bun standalone binary")
    ex.add_argument("-o", "--out", required=True, help="output .js path")
    ex.set_defaults(func=_cmd_extract)

    helper = sub.add_parser(
        "generate-multi-provider-helper",
        help="write the exact injected multi-provider JavaScript helper",
    )
    helper.add_argument("-o", "--out", required=True, help="output .js path")
    helper.set_defaults(func=_cmd_generate_multi_provider_helper)

    migration = sub.add_parser(
        "migrate-tool-ids",
        help="preview or back up and migrate legacy proxy tool IDs in transcripts",
        description="Stop Claude sessions before applying. Each affected transcript "
        "gets a private adjacent backup before replacement. Preview is the default.",
    )
    migration.add_argument(
        "--projects-dir",
        default="~/.claude/projects",
        help="transcript root (default: ~/.claude/projects)",
    )
    mode = migration.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply", action="store_true", help="back up and replace affected transcripts"
    )
    mode.add_argument(
        "--dry-run", action="store_true", help="preview only (the default)"
    )
    migration.set_defaults(func=_cmd_migrate_tool_ids)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
