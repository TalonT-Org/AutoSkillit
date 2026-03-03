"""Tests for autoskillit.pipeline.audit — pipeline failure tracking."""

from __future__ import annotations

import json

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
