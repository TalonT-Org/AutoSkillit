"""Tests for autoskillit.pipeline.audit — pipeline failure tracking."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.pipeline.audit import DefaultAuditLog, FailureRecord


def _make_record(**overrides: object) -> FailureRecord:
    defaults = dict(
        timestamp="2026-02-24T16:00:00Z",
        skill_command="/autoskillit:implement-worktree",
        exit_code=1,
        subtype="error",
        needs_retry=False,
        retry_reason="none",
        stderr="something went wrong",
    )
    return FailureRecord(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestFailureRecord:
    def test_to_dict_is_json_serializable(self):
        record = _make_record()
        d = record.to_dict()
        assert json.loads(json.dumps(d)) == d

    def test_to_dict_contains_all_fields(self):
        record = _make_record(skill_command="/test:cmd", exit_code=42)
        d = record.to_dict()
        assert d["skill_command"] == "/test:cmd"
        assert d["exit_code"] == 42


class TestDefaultAuditLog:
    def test_initially_empty(self):
        log = DefaultAuditLog()
        assert log.get_report() == []

    def test_record_failure_adds_entry(self):
        log = DefaultAuditLog()
        log.record_failure(_make_record())
        assert len(log.get_report()) == 1

    def test_get_report_returns_defensive_copy(self):
        log = DefaultAuditLog()
        log.record_failure(_make_record())
        report = log.get_report()
        report.clear()
        assert len(log.get_report()) == 1  # internal state unchanged

    def test_multiple_failures_accumulate(self):
        log = DefaultAuditLog()
        log.record_failure(_make_record(skill_command="/a"))
        log.record_failure(_make_record(skill_command="/b"))
        log.record_failure(_make_record(skill_command="/c"))
        assert len(log.get_report()) == 3

    def test_clear_resets_store(self):
        log = DefaultAuditLog()
        log.record_failure(_make_record())
        log.clear()
        assert log.get_report() == []

    def test_stderr_truncated_to_500_chars(self):
        long_stderr = "x" * 1000
        log = DefaultAuditLog()
        log.record_failure(_make_record(stderr=long_stderr))
        assert len(log.get_report()[0].stderr) == 500

    def test_skill_command_truncated_to_200_chars(self):
        long_cmd = "/autoskillit:implement-worktree " + "a" * 300
        log = DefaultAuditLog()
        log.record_failure(_make_record(skill_command=long_cmd))
        assert len(log.get_report()[0].skill_command) == 200

    def test_get_report_as_dicts_empty(self):
        log = DefaultAuditLog()
        assert log.get_report_as_dicts() == []

    def test_get_report_as_dicts_populated(self):
        log = DefaultAuditLog()
        log.record_failure(_make_record(skill_command="cmd", exit_code=1))
        dicts = log.get_report_as_dicts()
        assert len(dicts) == 1
        d = dicts[0]
        assert d["skill_command"] == "cmd"
        assert d["exit_code"] == 1
        assert "timestamp" in d
        assert "stderr" in d


def _write_audit_session(
    log_root: Path, dir_name: str, records: list, timestamp: str = "2026-03-07T00:00:00+00:00"
) -> None:
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "audit_log.json").write_text(json.dumps(records))
    index_entry = {"dir_name": dir_name, "timestamp": timestamp}
    with (log_root / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(index_entry) + "\n")


class TestDefaultAuditLogLoadFromLogDir:
    def _failure_dict(self, **overrides) -> dict:
        base = {
            "timestamp": "2026-03-07T00:00:00Z",
            "skill_command": "/autoskillit:implement-worktree",
            "exit_code": 1,
            "subtype": "error",
            "needs_retry": False,
            "retry_reason": "none",
            "stderr": "oops",
        }
        return {**base, **overrides}

    def test_restores_failure_records(self, tmp_path):
        """audit_log.json files in session dirs restore FailureRecord entries."""
        _write_audit_session(
            tmp_path, "s001", [self._failure_dict(skill_command="/foo", exit_code=2)]
        )
        log = DefaultAuditLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 1
        records = log.get_report()
        assert len(records) == 1
        assert records[0].skill_command == "/foo"
        assert records[0].exit_code == 2

    def test_since_filter(self, tmp_path):
        """Respects since= timestamp filter."""
        _write_audit_session(
            tmp_path, "old", [self._failure_dict()], timestamp="2025-01-01T00:00:00+00:00"
        )
        log = DefaultAuditLog()
        n = log.load_from_log_dir(tmp_path, since="2026-01-01T00:00:00+00:00")
        assert n == 0
        assert log.get_report() == []

    def test_returns_count(self, tmp_path):
        """Return value equals sessions loaded."""
        for i in range(3):
            _write_audit_session(tmp_path, f"s{i:03d}", [self._failure_dict(exit_code=i + 1)])
        log = DefaultAuditLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 3
