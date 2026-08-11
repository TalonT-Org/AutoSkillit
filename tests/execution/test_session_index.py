"""Tests for strict and tolerant retained session-index readers."""

from pathlib import Path

import pytest

from autoskillit.execution.session_index import read_tolerant_session_index_rows

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def test_tolerant_session_index_reader_skips_only_invalid_utf8_rows(tmp_path: Path) -> None:
    index = tmp_path / "sessions.jsonl"
    index.write_bytes(b'{"session_id":"one"}\n\xff\n{"session_id":"two"}\n')

    assert read_tolerant_session_index_rows(index) == [
        {"session_id": "one"},
        {"session_id": "two"},
    ]
