"""Tests for strict and tolerant retained session-index readers."""

from pathlib import Path

import pytest

from autoskillit.execution.session_index import (
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
