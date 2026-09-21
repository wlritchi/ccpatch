"""Tests for backup-first transcript tool-ID migration."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from ccpatch.cli import main
from ccpatch.transcripts import (
    MigrationError,
    encode_legacy_id,
    migrate_bytes,
    migrate_file,
    transcript_paths,
)

LEGACY = "call_first|fc_first"
ENCODED = "ccpatch_tc1_Y2FsbF9maXJzdHxmY19maXJzdA"


def _record(kind: str = "assistant", ident: str = LEGACY) -> dict[str, object]:
    block = (
        {"type": "tool_use", "id": ident, "name": "test", "input": {"id": LEGACY}}
        if kind == "assistant"
        else {"type": "tool_result", "tool_use_id": ident, "content": LEGACY}
    )
    return {"type": kind, "message": {"role": kind, "content": [block]}}


def _line(record: dict[str, object]) -> bytes:
    return json.dumps(record).encode() + b"\n"


@pytest.mark.parametrize(
    "ident", [LEGACY, "call_custom|ctc_custom", "call_a-b_2|fc_c-d_3"]
)
def test_codec_matches_proxy(ident: str) -> None:
    encoded = encode_legacy_id(ident)
    assert encoded.startswith("ccpatch_tc1_")
    suffix = encoded.removeprefix("ccpatch_tc1_")
    assert base64.urlsafe_b64decode(suffix + "=" * (-len(suffix) % 4)).decode() == ident
    assert encode_legacy_id(encoded) == encoded
    assert encode_legacy_id(LEGACY) == ENCODED


@pytest.mark.parametrize(
    "ident",
    [
        "toolu_native",
        ENCODED,
        "foo|bar",
        "call_a|unknown",
        "call_a|fc_",
        "call_a|fc_b|extra",
        "call_a|fc_b\n",
    ],
)
def test_unrecognized_and_encoded_ids_unchanged(ident: str) -> None:
    assert encode_legacy_id(ident) == ident


def test_calls_results_and_repeated_history() -> None:
    records = [_record(), _record("user"), _record(), _record("user", "toolu_native")]
    original = b"".join(_line(record) for record in records)
    amended, count = migrate_bytes(original)
    assert count == 3
    parsed = [json.loads(line) for line in amended.splitlines()]
    assert parsed[0]["message"]["content"][0]["id"] == ENCODED
    assert parsed[1]["message"]["content"][0]["tool_use_id"] == ENCODED
    assert parsed[2]["message"]["content"][0]["id"] == ENCODED
    assert parsed[0]["message"]["content"][0]["input"]["id"] == LEGACY
    assert parsed[1]["message"]["content"][0]["content"] == LEGACY
    assert amended.splitlines(keepends=True)[3] == original.splitlines(keepends=True)[3]
    assert migrate_bytes(amended) == (amended, 0)


def test_progress_and_metadata_references() -> None:
    progress: dict[str, object] = {
        "type": "progress",
        "toolUseID": LEGACY,
        "parentToolUseID": LEGACY,
        "data": {"message": _record(), "normalizedMessages": [_record("user")]},
    }
    records: list[dict[str, object]] = [
        progress,
        {"type": "attachment", "attachment": {"toolUseID": LEGACY}},
        {"type": "system", "toolUseID": LEGACY},
    ]
    amended, count = migrate_bytes(b"".join(_line(record) for record in records))
    assert count == 6
    assert amended.count(ENCODED.encode()) == 6


def test_does_not_rewrite_embedded_user_data() -> None:
    record = _record()
    record["toolUseResult"] = _record()
    record["quoted"] = {"toolUseID": LEGACY}
    amended, count = migrate_bytes(_line(record))
    assert count == 1
    parsed = json.loads(amended)
    assert parsed["toolUseResult"] == record["toolUseResult"]
    assert parsed["quoted"] == record["quoted"]


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_preserves_line_endings_and_unchanged_lines(ending: bytes) -> None:
    untouched = b'  { "type": "summary", "text": "hello" }  \r\n\n'
    source = untouched + _line(_record()).rstrip(b"\n") + ending
    amended, count = migrate_bytes(source)
    assert count == 1
    assert amended.startswith(untouched)
    assert amended[len(untouched) :].endswith(b"}" + ending)


@pytest.mark.parametrize(
    "bad",
    [
        b'{"unfinished":',
        b"[]\n",
        b' {"a":1,"a":2}\n',
        b'{"value":NaN}\n',
        b' {"value":"\xff"}\n',
    ],
)
def test_malformed_transcript_is_not_partially_written(
    tmp_path: Path, bad: bytes
) -> None:
    path = tmp_path / "session.jsonl"
    source = _line(_record()) + bad
    path.write_bytes(source)
    with pytest.raises(MigrationError, match="line 2"):
        migrate_file(path, apply=True)
    assert path.read_bytes() == source
    assert list(tmp_path.iterdir()) == [path]


def test_preview_backup_apply_and_idempotence(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    source = _line(_record()) + _line(_record("user"))
    path.write_bytes(source)
    path.chmod(0o640)
    assert migrate_file(path).replacements == 2
    assert path.read_bytes() == source
    assert len(list(tmp_path.iterdir())) == 1
    result = migrate_file(path, apply=True)
    assert result.backup is not None
    assert result.backup.read_bytes() == source
    assert result.backup.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_mode & 0o777 == 0o640
    assert result.replacements == 2
    assert migrate_file(path, apply=True).replacements == 0
    assert len(list(tmp_path.iterdir())) == 2
    assert transcript_paths(tmp_path) == [path]


def test_scans_subagents_but_not_symlinks(tmp_path: Path) -> None:
    nested = tmp_path / "project" / "session" / "subagents"
    nested.mkdir(parents=True)
    transcript = nested / "agent.jsonl"
    transcript.write_bytes(_line(_record()))
    (tmp_path / "link").symlink_to(nested, target_is_directory=True)
    (tmp_path / "alias.jsonl").symlink_to(transcript)
    (nested / "backup.jsonl.bak").write_bytes(_line(_record()))
    assert transcript_paths(tmp_path) == [transcript]


def test_rejects_hardlinked_and_symlink_transcripts(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(_line(_record()))
    link = tmp_path / "link.jsonl"
    link.symlink_to(path)
    with pytest.raises(OSError):
        migrate_file(link, apply=True)
    link.unlink()
    os.link(path, link)
    with pytest.raises(MigrationError, match="one link"):
        migrate_file(path, apply=True)


def test_concurrent_append_refused(tmp_path: Path, mocker: MockerFixture) -> None:
    path = tmp_path / "session.jsonl"
    source = _line(_record())
    path.write_bytes(source)
    original = os.fsync

    def append_on_backup(descriptor: int) -> None:
        original(descriptor)
        if path.read_bytes() == source:
            with path.open("ab") as stream:
                stream.write(b' {"type":"summary"}\n')

    mocker.patch("ccpatch.transcripts.os.fsync", side_effect=append_on_backup)
    with pytest.raises(MigrationError, match="changed during migration"):
        migrate_file(path, apply=True)
    assert path.read_bytes().startswith(source)
    assert len(list(tmp_path.glob("*.bak"))) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_backup_failure_never_replaces_transcript(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = tmp_path / "session.jsonl"
    source = _line(_record())
    path.write_bytes(source)
    mocker.patch("ccpatch.transcripts.os.fsync", side_effect=OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        migrate_file(path, apply=True)
    assert path.read_bytes() == source


def test_replace_failure_keeps_backup(tmp_path: Path, mocker: MockerFixture) -> None:
    path = tmp_path / "session.jsonl"
    source = _line(_record())
    path.write_bytes(source)
    mocker.patch("ccpatch.transcripts.os.replace", side_effect=OSError("denied"))
    with pytest.raises(OSError, match="denied"):
        migrate_file(path, apply=True)
    assert path.read_bytes() == source
    assert next(tmp_path.glob("*.bak")).read_bytes() == source
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_preview_apply_and_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "session.jsonl"
    source = _line(_record())
    path.write_bytes(source)
    args = ["migrate-tool-ids", "--projects-dir", str(tmp_path)]
    assert main(args) == 0
    assert "would update" in capsys.readouterr().out
    assert path.read_bytes() == source
    assert main([*args, "--dry-run"]) == 0
    assert main([*args, "--apply"]) == 0
    assert ENCODED.encode() in path.read_bytes()
    (tmp_path / "broken.jsonl").write_bytes(b"not-json")
    assert main(args) == 1
    assert "invalid transcript" in capsys.readouterr().err
    assert main(["migrate-tool-ids", "--projects-dir", str(tmp_path / "absent")]) == 1


def test_preserves_numeric_literals_in_changed_records() -> None:
    source = b'{"message":{"content":[{"type":"tool_use","id":"call_first|fc_first"}]},"numbers":[1e999,-0,1.234567890123456789,100000000000000000000000000000000000]}\n'
    amended, count = migrate_bytes(source)
    assert count == 1
    assert amended == source.replace(LEGACY.encode(), ENCODED.encode())


def test_default_root(tmp_path: Path, mocker: MockerFixture) -> None:
    mocker.patch.dict(os.environ, {"HOME": str(tmp_path)})
    root = tmp_path / ".claude" / "projects"
    root.mkdir(parents=True)
    path = root / "session.jsonl"
    source = _line(_record())
    path.write_bytes(source)
    assert main(["migrate-tool-ids"]) == 0
    assert path.read_bytes() == source
