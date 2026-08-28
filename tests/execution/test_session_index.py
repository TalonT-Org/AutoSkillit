"""Tests for strict and tolerant retained session-index readers."""

import json
from pathlib import Path

import pytest

from autoskillit.execution.session_index import (
    find_stale_session_archive_references,
    read_session_index_rows,
    read_tolerant_session_index_rows,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def test_strict_session_index_reader_enforces_shape_and_budget(tmp_path: Path) -> None:
    index = tmp_path / "sessions.jsonl"
    index.write_text('{"session_id":"one"}\n')
    assert read_session_index_rows(index) == [{"session_id": "one"}]

    index.write_text('{"session_id":"one"}\nnot-json\n')
    with pytest.raises(ValueError, match="Malformed session index row 2"):
        read_session_index_rows(index)

    index.write_text("[]\n")
    with pytest.raises(ValueError, match="not an object"):
        read_session_index_rows(index)

    index.write_text('{"session_id":"one"}\n')
    with pytest.raises(ValueError, match="byte budget"):
        read_session_index_rows(index, max_bytes=5)


def test_strict_session_index_reader_rejects_partial_suffix(tmp_path: Path) -> None:
    index = tmp_path / "sessions.jsonl"
    index.write_text('{"session_id":"one"}')

    with pytest.raises(ValueError, match="incomplete row"):
        read_session_index_rows(index)


def test_strict_session_index_reader_rejects_invalid_utf8(tmp_path: Path) -> None:
    index = tmp_path / "sessions.jsonl"
    index.write_bytes(b"\xff\n")

    with pytest.raises(ValueError, match="not valid UTF-8"):
        read_session_index_rows(index)


def test_tolerant_session_index_reader_skips_only_invalid_utf8_rows(tmp_path: Path) -> None:
    index = tmp_path / "sessions.jsonl"
    index.write_bytes(b'{"session_id":"one"}\n\xff\n{"session_id":"two"}\n')

    assert read_tolerant_session_index_rows(index) == [
        {"session_id": "one"},
        {"session_id": "two"},
    ]


def test_find_stale_session_archive_references_is_tolerant_and_path_scoped(
    tmp_path: Path,
) -> None:
    existing_dir = tmp_path / "existing-dir"
    existing_dir.mkdir()
    existing_file = tmp_path / "existing.log"
    existing_file.write_text("")
    missing_dir = tmp_path / "missing-dir"
    missing_file = tmp_path / "missing.log"
    archive = tmp_path / "sessions-archive.jsonl"
    rows = [
        {
            "cwd": str(existing_dir),
            "claude_code_log": str(missing_file),
            "codex_log": "relative/codex.jsonl",
        },
        {
            "cwd": str(missing_dir),
            "claude_code_log": str(existing_file),
            "codex_log": None,
        },
        {"cwd": 7, "claude_code_log": [], "codex_log": {}},
    ]
    archive.write_text(
        "".join(json.dumps(row) + "\n" for row in rows) + '"scalar"\n[]\n{"cwd":"truncated"'
    )

    assert find_stale_session_archive_references(tmp_path) == [
        str(missing_file),
        str(missing_dir),
    ]


def test_find_stale_session_archive_references_deduplicates_paths(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing")
    archive = tmp_path / "sessions-archive.jsonl"
    archive.write_text(
        json.dumps({"cwd": missing, "claude_code_log": missing})
        + "\n"
        + json.dumps({"codex_log": missing})
        + "\n"
    )

    assert find_stale_session_archive_references(tmp_path) == [missing]
