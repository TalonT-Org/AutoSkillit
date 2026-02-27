"""Tests for autoskillit._audit — pipeline failure tracking."""

from __future__ import annotations

import json
from dataclasses import fields

from autoskillit._audit import AuditLog, FailureRecord


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
    def test_has_all_required_fields(self):
        record = _make_record()
        field_names = {f.name for f in fields(record)}
        assert {
            "timestamp",
            "skill_command",
            "exit_code",
            "subtype",
            "needs_retry",
            "retry_reason",
            "stderr",
        } <= field_names

    def test_to_dict_is_json_serializable(self):
        record = _make_record()
        d = record.to_dict()
        json.dumps(d)  # must not raise

    def test_to_dict_contains_all_fields(self):
        record = _make_record(skill_command="/test:cmd", exit_code=42)
        d = record.to_dict()
        assert d["skill_command"] == "/test:cmd"
        assert d["exit_code"] == 42


class TestAuditLog:
    def test_initially_empty(self):
        log = AuditLog()
        assert log.get_report() == []

    def test_record_failure_adds_entry(self):
        log = AuditLog()
        log.record_failure(_make_record())
        assert len(log.get_report()) == 1

    def test_get_report_returns_defensive_copy(self):
        log = AuditLog()
        log.record_failure(_make_record())
        report = log.get_report()
        report.clear()
        assert len(log.get_report()) == 1  # internal state unchanged

    def test_multiple_failures_accumulate(self):
        log = AuditLog()
        log.record_failure(_make_record(skill_command="/a"))
        log.record_failure(_make_record(skill_command="/b"))
        log.record_failure(_make_record(skill_command="/c"))
        assert len(log.get_report()) == 3

    def test_clear_resets_store(self):
        log = AuditLog()
        log.record_failure(_make_record())
        log.clear()
        assert log.get_report() == []

    def test_stderr_truncated_to_500_chars(self):
        long_stderr = "x" * 1000
        log = AuditLog()
        log.record_failure(_make_record(stderr=long_stderr))
        assert len(log.get_report()[0].stderr) <= 500

    def test_skill_command_truncated_to_200_chars(self):
        long_cmd = "/autoskillit:implement-worktree " + "a" * 300
        log = AuditLog()
        log.record_failure(_make_record(skill_command=long_cmd))
        assert len(log.get_report()[0].skill_command) <= 200


class TestAuditLogModuleSingleton:
    def test_module_singleton_exists(self):
        from autoskillit._audit import _audit_log

        assert isinstance(_audit_log, AuditLog)

    def test_singleton_is_importable_from_audit(self):
        from autoskillit._audit import _audit_log  # always in _audit, injected into ToolContext

        assert isinstance(_audit_log, AuditLog)
