"""Migrate legacy proxy tool IDs in Claude Code JSONL transcripts."""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

TOOL_ID_PREFIX = "ccpatch_tc1_"
_LEGACY_ID = re.compile(r"call_[A-Za-z0-9_-]+\|(?:fc|ctc)_[A-Za-z0-9_-]+\Z")
_REFERENCE_KEYS = ("toolUseID", "parentToolUseID")


class MigrationError(ValueError):
    """A transcript cannot be migrated safely."""


@dataclass(frozen=True)
class MigrationResult:
    path: Path
    replacements: int
    backup: Path | None = None


def encode_legacy_id(value: str) -> str:
    """Use the proxy's version-one envelope for recognized legacy IDs."""
    if not _LEGACY_ID.fullmatch(value):
        return value
    encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
    return TOOL_ID_PREFIX + encoded.rstrip("=")


def _replace_id(record: dict[str, object], key: str) -> int:
    value = record.get(key)
    if not isinstance(value, str):
        return 0
    encoded = encode_legacy_id(value)
    if encoded == value:
        return 0
    record[key] = encoded
    return 1


def _message(message: object) -> int:
    if not isinstance(message, dict):
        return 0
    content = message.get("content")
    if not isinstance(content, list):
        return 0
    count = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            count += _replace_id(block, "id")
        elif block.get("type") == "tool_result":
            count += _replace_id(block, "tool_use_id")
    return count


def _record(record: object) -> int:
    if not isinstance(record, dict):
        return 0
    count = _message(record.get("message"))
    for key in _REFERENCE_KEYS:
        count += _replace_id(record, key)
    attachment = record.get("attachment")
    if isinstance(attachment, dict):
        count += _replace_id(attachment, "toolUseID")
    if record.get("type") == "progress":
        data = record.get("data")
        if isinstance(data, dict):
            count += _record(data.get("message"))
            messages = data.get("normalizedMessages")
            if isinstance(messages, list):
                count += sum(_record(message) for message in messages)
    return count


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise MigrationError(f"invalid JSON constant {value}")


@dataclass(frozen=True)
class _JsonNumber:
    literal: str


def _dump_json(value: object) -> str:
    if isinstance(value, _JsonNumber):
        return value.literal
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key) + ":" + _dump_json(item) for key, item in value.items()
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(_dump_json(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def migrate_bytes(source: bytes) -> tuple[bytes, int]:
    """Validate all records before producing a replacement transcript."""
    output: list[bytes] = []
    count = 0
    for number, line in enumerate(source.splitlines(keepends=True), 1):
        if not line.strip():
            output.append(line)
            continue
        try:
            record = json.loads(
                line.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
                parse_float=_JsonNumber,
                parse_int=_JsonNumber,
            )
            if not isinstance(record, dict):
                raise MigrationError("transcript record is not a JSON object")
            changed = _record(record)
            if changed:
                ending = (
                    b"\r\n"
                    if line.endswith(b"\r\n")
                    else b"\n"
                    if line.endswith(b"\n")
                    else b""
                )
                line = _dump_json(record).encode("utf-8") + ending
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise MigrationError(f"invalid transcript at line {number}: {exc}") from exc
        output.append(line)
        count += changed
    return b"".join(output), count


def transcript_paths(root: Path) -> list[Path]:
    """Find transcripts without following directory or file symlinks."""
    if root.is_symlink() or not root.is_dir():
        raise MigrationError(f"not a non-symlink projects directory: {root}")
    paths: list[Path] = []

    def on_error(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=on_error):
        dirs[:] = sorted(
            name for name in dirs if not (Path(directory) / name).is_symlink()
        )
        paths.extend(
            Path(directory) / name
            for name in sorted(files)
            if name.endswith(".jsonl") and not (Path(directory) / name).is_symlink()
        )
    return paths


def _snapshot(path: Path) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise MigrationError("transcript must be a regular file with one link")
        data = stream.read()
        after = os.fstat(stream.fileno())
        if _identity(before) != _identity(after):
            raise MigrationError(
                "transcript changed while it was read; stop its session"
            )
    return data, after


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _assert_unchanged(path: Path, source: bytes, info: os.stat_result) -> None:
    current, current_info = _snapshot(path)
    if current != source or _identity(current_info) != _identity(info):
        raise MigrationError("transcript changed during migration; stop its session")


def migrate_file(path: Path, *, apply: bool = False) -> MigrationResult:
    source, info = _snapshot(path)
    amended, count = migrate_bytes(source)
    if not count or not apply:
        return MigrationResult(path, count)
    if info.st_uid != os.getuid():
        raise MigrationError("refusing to replace a transcript owned by another user")
    backup = path.with_name(f"{path.name}.ccpatch-tool-ids-{uuid4().hex}.bak")
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(source)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(amended)
            stream.flush()
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(stream.fileno())
        _assert_unchanged(path, source, info)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
            os.replace(staged, path)
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        staged.unlink(missing_ok=True)
    return MigrationResult(path, count, backup)
