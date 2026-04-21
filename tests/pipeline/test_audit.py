"""Tests for autoskillit.pipeline.audit — pipeline failure tracking."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.pipeline.audit import (
    DefaultAuditLog,
    FailureRecord,
    _validate_failure_record_dict,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _valid_failure_record_dict(**overrides: object) -> dict:
    """Module-level factory for valid failure record dicts.

    Used by TestValidateFailureRecordDict and TestLoadFromLogDirTypeValidation.
    """
    base: dict = {
        "timestamp": "2026-03-28T00:00:00Z",
        "skill_command": "/autoskillit:implement-worktree",
        "exit_code": 1,
        "subtype": "error",
        "needs_retry": False,
        "retry_reason": "none",
        "stderr": "oops",
    }
    return {**base, **overrides}


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


class TestDefaultAuditLogBudget:
    """DefaultAuditLog.consecutive_failures and record_success."""

    def test_consecutive_failures_empty(self) -> None:
        log = DefaultAuditLog()
        assert log.consecutive_failures("/autoskillit:open-pr") == 0

    def test_consecutive_failures_single_retry(self) -> None:
        log = DefaultAuditLog()
        log.record_failure(
            _make_record(
                skill_command="/autoskillit:open-pr", needs_retry=True, retry_reason="resume"
            )
        )
        assert log.consecutive_failures("/autoskillit:open-pr") == 1

    def test_consecutive_failures_multiple(self) -> None:
        log = DefaultAuditLog()
        for _ in range(3):
            log.record_failure(
                _make_record(
                    skill_command="/autoskillit:open-pr", needs_retry=True, retry_reason="resume"
                )
            )
        assert log.consecutive_failures("/autoskillit:open-pr") == 3

    def test_consecutive_failures_reset_by_success_sentinel(self) -> None:
        log = DefaultAuditLog()
        for _ in range(3):
            log.record_failure(
                _make_record(
                    skill_command="/autoskillit:open-pr", needs_retry=True, retry_reason="resume"
                )
            )
        log.record_success("/autoskillit:open-pr")
        assert log.consecutive_failures("/autoskillit:open-pr") == 0

    def test_consecutive_failures_ignores_other_commands(self) -> None:
        log = DefaultAuditLog()
        for _ in range(3):
            log.record_failure(
                _make_record(
                    skill_command="/autoskillit:other", needs_retry=True, retry_reason="resume"
                )
            )
        assert log.consecutive_failures("/autoskillit:open-pr") == 0

    def test_consecutive_failures_reset_by_terminal_failure(self) -> None:
        """A needs_retry=False record for the same command resets the streak."""
        log = DefaultAuditLog()
        log.record_failure(
            _make_record(
                skill_command="/autoskillit:open-pr", needs_retry=True, retry_reason="resume"
            )
        )
        log.record_failure(
            _make_record(
                skill_command="/autoskillit:open-pr", needs_retry=False, retry_reason="none"
            )
        )
        assert log.consecutive_failures("/autoskillit:open-pr") == 0

    def test_consecutive_failures_counts_after_success_reset(self) -> None:
        """New failures after a success reset are counted from the reset point."""
        log = DefaultAuditLog()
        for _ in range(3):
            log.record_failure(
                _make_record(
                    skill_command="/autoskillit:open-pr", needs_retry=True, retry_reason="resume"
                )
            )
        log.record_success("/autoskillit:open-pr")
        # One more failure after success
        log.record_failure(
            _make_record(
                skill_command="/autoskillit:open-pr", needs_retry=True, retry_reason="resume"
            )
        )
        assert log.consecutive_failures("/autoskillit:open-pr") == 1

    def test_record_success_sentinel_visible_in_failure_report(self) -> None:
        """Success sentinels are visible in get_report but with subtype='success'."""
        log = DefaultAuditLog()
        log.record_success("/autoskillit:open-pr")
        report = log.get_report()
        assert len(report) == 1
        assert report[0].subtype == "success"
        assert report[0].needs_retry is False


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


class TestIterSessionLogEntries:
    """Tests for the _iter_session_log_entries shared generator (P6-F1)."""

    def test_iter_session_log_entries_yields_matching_files(self, tmp_path):
        import json

        from autoskillit.pipeline.audit import _iter_session_log_entries

        session_dir = tmp_path / "sessions" / "s001"
        session_dir.mkdir(parents=True)
        target = session_dir / "audit_log.json"
        target.write_text("[]")
        (tmp_path / "sessions.jsonl").write_text(
            json.dumps({"dir_name": "s001", "timestamp": "2026-03-07T00:00:00+00:00"}) + "\n"
        )

        paths = list(_iter_session_log_entries(tmp_path, "", "audit_log.json"))
        assert paths == [target]

    def test_iter_session_log_entries_skips_missing_file(self, tmp_path):
        import json

        from autoskillit.pipeline.audit import _iter_session_log_entries

        session_dir = tmp_path / "sessions" / "no-file"
        session_dir.mkdir(parents=True)
        (tmp_path / "sessions.jsonl").write_text(
            json.dumps({"dir_name": "no-file", "timestamp": "2026-03-07T00:00:00+00:00"}) + "\n"
        )
        paths = list(_iter_session_log_entries(tmp_path, "", "audit_log.json"))
        assert paths == []

    def test_iter_session_log_entries_since_filter(self, tmp_path):
        import json

        from autoskillit.pipeline.audit import _iter_session_log_entries

        session_dir = tmp_path / "sessions" / "old"
        session_dir.mkdir(parents=True)
        (session_dir / "audit_log.json").write_text("[]")
        (tmp_path / "sessions.jsonl").write_text(
            json.dumps({"dir_name": "old", "timestamp": "2025-01-01T00:00:00+00:00"}) + "\n"
        )
        paths = list(
            _iter_session_log_entries(tmp_path, "2026-01-01T00:00:00+00:00", "audit_log.json")
        )
        assert paths == []

    def test_iter_session_log_entries_no_index_returns_empty(self, tmp_path):
        from autoskillit.pipeline.audit import _iter_session_log_entries

        paths = list(_iter_session_log_entries(tmp_path, "", "audit_log.json"))
        assert paths == []


def test_iter_session_log_entries_kitchen_id_filter(tmp_path):
    """kitchen_id_filter yields only entries with matching kitchen_id."""
    import json

    from autoskillit.pipeline.audit import _iter_session_log_entries

    entries = [
        {
            "session_id": "a",
            "dir_name": "a",
            "timestamp": "2026-03-27T08:00:00",
            "cwd": "/work",
            "kitchen_id": "run-1",
            "step_name": "plan",
        },
        {
            "session_id": "b",
            "dir_name": "b",
            "timestamp": "2026-03-27T08:01:00",
            "cwd": "/work",
            "kitchen_id": "run-1",
            "step_name": "implement",
        },
        {
            "session_id": "c",
            "dir_name": "c",
            "timestamp": "2026-03-27T08:02:00",
            "cwd": "/work",
            "kitchen_id": "run-2",
            "step_name": "plan",
        },
    ]
    (tmp_path / "sessions.jsonl").write_text("\n".join(json.dumps(e) for e in entries))
    for e in entries:  # dummy token files so entries pass file-existence check
        d = tmp_path / "sessions" / e["dir_name"]
        d.mkdir(parents=True)
        (d / "token_usage.json").write_text(
            json.dumps(
                {
                    "step_name": e["step_name"],
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "timing_seconds": 1.0,
                }
            )
        )

    results = list(
        _iter_session_log_entries(
            tmp_path, since="", filename="token_usage.json", kitchen_id_filter="run-1"
        )
    )
    assert len(results) == 2  # 2 sessions with kitchen_id="run-1"

    # Verify the filter is directional — run-2 has only 1 session
    results_run2 = list(
        _iter_session_log_entries(
            tmp_path, since="", filename="token_usage.json", kitchen_id_filter="run-2"
        )
    )
    assert len(results_run2) == 1


def test_iter_session_log_entries_kitchen_id_backward_compat(tmp_path):
    """kitchen_id_filter falls back to pipeline_id key in old sessions.jsonl entries."""
    import json

    from autoskillit.pipeline.audit import _iter_session_log_entries

    # Old-format entries that use pipeline_id key (not kitchen_id)
    entries = [
        {
            "session_id": "old-a",
            "dir_name": "old-a",
            "timestamp": "2026-03-27T08:00:00",
            "cwd": "/work",
            "pipeline_id": "legacy-run",  # old key
            "step_name": "plan",
        },
        {
            "session_id": "old-b",
            "dir_name": "old-b",
            "timestamp": "2026-03-27T08:01:00",
            "cwd": "/work",
            "pipeline_id": "other-run",  # different old key
            "step_name": "implement",
        },
    ]
    (tmp_path / "sessions.jsonl").write_text("\n".join(json.dumps(e) for e in entries))
    for e in entries:
        d = tmp_path / "sessions" / e["dir_name"]
        d.mkdir(parents=True)
        (d / "token_usage.json").write_text(
            json.dumps(
                {
                    "step_name": e["step_name"],
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "timing_seconds": 0.5,
                }
            )
        )

    # kitchen_id_filter should find old entries via pipeline_id fallback
    results = list(
        _iter_session_log_entries(
            tmp_path, since="", filename="token_usage.json", kitchen_id_filter="legacy-run"
        )
    )
    assert len(results) == 1


class TestValidateFailureRecordDict:
    def test_valid_dict_returns_true(self):
        assert _validate_failure_record_dict(_valid_failure_record_dict()) is True

    def test_missing_key_returns_false(self):
        d = _valid_failure_record_dict()
        del d["stderr"]
        assert _validate_failure_record_dict(d) is False

    def test_wrong_type_exit_code_returns_false(self):
        assert _validate_failure_record_dict(_valid_failure_record_dict(exit_code="bad")) is False

    def test_wrong_type_needs_retry_returns_false(self):
        # "true" is a str, not bool
        assert (
            _validate_failure_record_dict(_valid_failure_record_dict(needs_retry="true")) is False
        )

    def test_wrong_type_timestamp_returns_false(self):
        assert _validate_failure_record_dict(_valid_failure_record_dict(timestamp=12345)) is False

    def test_int_for_bool_field_returns_false(self):
        # 0 and 1 are int, not bool — must be rejected for needs_retry: bool
        assert _validate_failure_record_dict(_valid_failure_record_dict(needs_retry=1)) is False

    def test_extra_keys_are_ignored(self):
        d = _valid_failure_record_dict()
        d["extra_unexpected_key"] = "ignored"
        assert _validate_failure_record_dict(d) is True


class TestLoadFromLogDirTypeValidation:
    def test_wrong_type_exit_code_is_skipped(self, tmp_path):
        """record_dict with exit_code as str is skipped, not silently accepted."""
        _write_audit_session(
            tmp_path, "s001", [_valid_failure_record_dict(exit_code="not-an-int")]
        )
        log = DefaultAuditLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 0
        assert log.get_report() == []

    def test_wrong_type_needs_retry_is_skipped(self, tmp_path):
        """record_dict with needs_retry as str is skipped."""
        _write_audit_session(tmp_path, "s001", [_valid_failure_record_dict(needs_retry="true")])
        log = DefaultAuditLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 0

    def test_missing_field_is_skipped(self, tmp_path):
        """record_dict missing a required field is skipped."""
        bad = _valid_failure_record_dict()
        del bad["retry_reason"]
        _write_audit_session(tmp_path, "s001", [bad])
        log = DefaultAuditLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 0

    def test_valid_record_alongside_invalid_is_preserved(self, tmp_path):
        """A valid record in the same session file is loaded despite invalid siblings."""
        records = [
            _valid_failure_record_dict(exit_code="bad"),  # skipped
            _valid_failure_record_dict(skill_command="/ok", exit_code=2),  # kept
        ]
        _write_audit_session(tmp_path, "s001", records)
        log = DefaultAuditLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 1
        assert log.get_report()[0].skill_command == "/ok"
