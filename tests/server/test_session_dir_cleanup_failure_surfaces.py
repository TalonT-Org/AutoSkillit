"""B8: a failed session-teardown cleanup is a durable, queryable record, not a warning-only
log line."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_execution._run_skill_dispatch import (
    _record_cleanup_failure,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_cleanup_failure_is_recorded_durably(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path))

    _record_cleanup_failure("sess-1", "/dev/shm/autoskillit-sessions/sess-1", OSError("busy"))

    record_path = tmp_path / "cleanup_failures.jsonl"
    assert record_path.is_file()
    lines = [json.loads(line) for line in record_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["session_id"] == "sess-1"
    assert lines[0]["path"] == "/dev/shm/autoskillit-sessions/sess-1"
    assert lines[0]["exception_type"] == "OSError"
    assert lines[0]["message"] == "busy"


def test_cleanup_failure_records_accumulate_and_stay_bounded(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path))

    for i in range(3):
        _record_cleanup_failure(f"sess-{i}", None, RuntimeError(f"fail-{i}"))

    record_path = tmp_path / "cleanup_failures.jsonl"
    lines = [json.loads(line) for line in record_path.read_text().splitlines() if line.strip()]
    assert [line["session_id"] for line in lines] == ["sess-0", "sess-1", "sess-2"]
