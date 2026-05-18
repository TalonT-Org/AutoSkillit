"""Tests for pipeline.tokens v1 backward compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.pipeline.tokens import DefaultTokenLog

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _write_minimal_v1_session(
    log_root: Path, dir_name: str, tu_data: dict, timestamp: str = "2025-01-15T10:00:00+00:00"
) -> None:
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "token_usage.json").write_text(json.dumps(tu_data))
    index_entry = {"dir_name": dir_name, "timestamp": timestamp}
    with (log_root / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(index_entry) + "\n")


def test_load_from_log_dir_v1_fields_no_double_count(tmp_path: Path) -> None:
    tu_data = {
        "session_label": "implement",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_write_tokens": 10,
        "cache_read_tokens": 5,
        "cache_creation_input_tokens": 99,
        "cache_read_input_tokens": 88,
        "timing_seconds": 8.0,
        "schema_version": 2,
    }
    _write_minimal_v1_session(tmp_path, "s1", tu_data)

    log = DefaultTokenLog()
    n = log.load_from_log_dir(tmp_path)
    report = log.get_report()

    assert n == 1
    assert len(report) == 1
    assert report[0]["cache_write_tokens"] == 10
    assert report[0]["cache_read_tokens"] == 5
