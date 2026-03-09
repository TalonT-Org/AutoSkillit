"""Tests for autoskillit.pipeline.tokens — pipeline token usage tracking."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from autoskillit.pipeline.tokens import DefaultTokenLog, TokenEntry


def _make_usage(**overrides: int) -> dict[str, int]:
    defaults = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 5,
    }
    return {**defaults, **overrides}


class TestTokenEntry:
    def test_fields_exist(self):
        entry = TokenEntry(step_name="plan")
        field_names = {f.name for f in fields(entry)}
        assert field_names == {
            "step_name",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "invocation_count",
            "elapsed_seconds",
        }

    def test_default_counts_are_zero(self):
        entry = TokenEntry(step_name="plan")
        assert entry.input_tokens == 0
        assert entry.output_tokens == 0
        assert entry.cache_creation_input_tokens == 0
        assert entry.cache_read_input_tokens == 0
        assert entry.invocation_count == 0

    def test_to_dict_is_json_serializable(self):
        entry = TokenEntry(step_name="plan")
        d = entry.to_dict()
        assert json.loads(json.dumps(d)) == d

    def test_to_dict_contains_all_fields(self):
        entry = TokenEntry(step_name="implement", input_tokens=42)
        d = entry.to_dict()
        assert set(d.keys()) == {
            "step_name",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "invocation_count",
            "elapsed_seconds",
        }
        assert d["step_name"] == "implement"
        assert d["input_tokens"] == 42


class TestDefaultTokenLog:
    def test_empty_on_init(self):
        log = DefaultTokenLog()
        assert log.get_report() == []

    def test_record_single_step(self):
        log = DefaultTokenLog()
        log.record("plan", _make_usage())
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["input_tokens"] == 100
        assert report[0]["output_tokens"] == 50
        assert report[0]["cache_creation_input_tokens"] == 10
        assert report[0]["cache_read_input_tokens"] == 5

    def test_record_same_step_twice_accumulates(self):
        log = DefaultTokenLog()
        log.record("implement", _make_usage(input_tokens=100, output_tokens=50))
        log.record("implement", _make_usage(input_tokens=200, output_tokens=80))
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["input_tokens"] == 300
        assert report[0]["output_tokens"] == 130

    def test_invocation_count_increments_per_call(self):
        log = DefaultTokenLog()
        log.record("implement", _make_usage())
        log.record("implement", _make_usage())
        report = log.get_report()
        assert report[0]["invocation_count"] == 2

    def test_record_different_steps_produces_separate_entries(self):
        log = DefaultTokenLog()
        log.record("plan", _make_usage())
        log.record("implement", _make_usage())
        report = log.get_report()
        assert len(report) == 2
        assert report[0]["step_name"] == "plan"
        assert report[1]["step_name"] == "implement"

    @pytest.mark.parametrize("step,usage", [("", _make_usage()), ("plan", None)])
    def test_record_is_noop_for_invalid_input(self, step, usage):
        log = DefaultTokenLog()
        log.record(step, usage)
        assert log.get_report() == []

    def test_get_report_is_defensive_copy(self):
        log = DefaultTokenLog()
        log.record("plan", _make_usage())
        report = log.get_report()
        report.clear()
        assert len(log.get_report()) == 1

    def test_clear_resets_all_entries(self):
        log = DefaultTokenLog()
        log.record("plan", _make_usage())
        log.record("implement", _make_usage())
        log.record("verify", _make_usage())
        log.clear()
        assert log.get_report() == []

    def test_partial_token_fields_default_missing_to_zero(self):
        log = DefaultTokenLog()
        log.record("plan", {"input_tokens": 42})
        report = log.get_report()
        assert report[0]["input_tokens"] == 42
        assert report[0]["output_tokens"] == 0
        assert report[0]["cache_creation_input_tokens"] == 0
        assert report[0]["cache_read_input_tokens"] == 0

    def test_compute_total_empty_log(self):
        log = DefaultTokenLog()
        total = log.compute_total()
        assert total == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_elapsed_seconds": 0.0,
        }

    def test_token_entry_has_elapsed_seconds_field(self):
        entry = TokenEntry(step_name="foo")
        assert entry.elapsed_seconds == 0.0

    def test_token_entry_elapsed_seconds_in_to_dict(self):
        entry = TokenEntry(step_name="foo")
        d = entry.to_dict()
        assert "elapsed_seconds" in d
        assert d["elapsed_seconds"] == 0.0

    def test_record_accumulates_elapsed_seconds(self):
        log = DefaultTokenLog()
        start = "2026-01-01T00:00:00+00:00"
        end = "2026-01-01T00:00:10+00:00"
        log.record("step1", {"input_tokens": 100}, start_ts=start, end_ts=end)
        entries = log.get_report()
        assert len(entries) == 1
        assert entries[0]["elapsed_seconds"] == pytest.approx(10.0)

    def test_record_accumulates_elapsed_seconds_across_invocations(self):
        log = DefaultTokenLog()
        start1 = "2026-01-01T00:00:00+00:00"
        end1 = "2026-01-01T00:00:10+00:00"
        start2 = "2026-01-01T00:01:00+00:00"
        end2 = "2026-01-01T00:01:05+00:00"
        log.record("step1", {"input_tokens": 50}, start_ts=start1, end_ts=end1)
        log.record("step1", {"input_tokens": 50}, start_ts=start2, end_ts=end2)
        entries = log.get_report()
        assert entries[0]["elapsed_seconds"] == pytest.approx(15.0)

    def test_record_no_timing_leaves_elapsed_at_zero(self):
        log = DefaultTokenLog()
        log.record("step1", {"input_tokens": 100})
        entries = log.get_report()
        assert entries[0]["elapsed_seconds"] == 0.0

    def test_record_partial_timing_leaves_elapsed_at_zero(self):
        log = DefaultTokenLog()
        log.record("step1", {"input_tokens": 100}, start_ts="2026-01-01T00:00:00+00:00")
        entries = log.get_report()
        assert entries[0]["elapsed_seconds"] == 0.0

    def test_compute_total_includes_total_elapsed_seconds(self):
        log = DefaultTokenLog()
        log.record(
            "a",
            {"input_tokens": 10},
            start_ts="2026-01-01T00:00:00+00:00",
            end_ts="2026-01-01T00:00:05+00:00",
        )
        log.record(
            "b",
            {"input_tokens": 20},
            start_ts="2026-01-01T00:01:00+00:00",
            end_ts="2026-01-01T00:01:07+00:00",
        )
        total = log.compute_total()
        assert "total_elapsed_seconds" in total
        assert total["total_elapsed_seconds"] == pytest.approx(12.0)

    def test_compute_total_elapsed_seconds_empty_log(self):
        log = DefaultTokenLog()
        total = log.compute_total()
        assert total["total_elapsed_seconds"] == 0.0

    def test_compute_total_accumulates_all_four_types(self):
        log = DefaultTokenLog()
        log.record(
            "stepA",
            {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 3,
            },
        )
        log.record(
            "stepB",
            {
                "input_tokens": 100,
                "output_tokens": 200,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 30,
            },
        )
        total = log.compute_total()
        assert total["input_tokens"] == 110
        assert total["output_tokens"] == 220
        assert total["cache_creation_input_tokens"] == 55
        assert total["cache_read_input_tokens"] == 33

    def test_record_backward_clock_elapsed_is_non_negative(self):
        """elapsed_seconds must never go negative even when end_ts < start_ts."""
        log = DefaultTokenLog()
        log.record(
            "step",
            {"input_tokens": 1, "output_tokens": 1},
            start_ts="2026-01-01T12:05:00+00:00",  # later
            end_ts="2026-01-01T12:00:00+00:00",  # earlier
        )
        entries = log.get_report()
        assert entries[0]["elapsed_seconds"] >= 0

    def test_record_uses_elapsed_seconds_param_over_iso_subtraction(self):
        """When elapsed_seconds kwarg is provided, it is used directly, not ISO subtraction."""
        log = DefaultTokenLog()
        log.record(
            "step",
            {"input_tokens": 1, "output_tokens": 1},
            start_ts="2026-01-01T12:00:00+00:00",
            end_ts="2026-01-01T12:00:05+00:00",  # ISO implies 5.0s
            elapsed_seconds=12.5,  # monotonic says 12.5s
        )
        entries = log.get_report()
        assert entries[0]["elapsed_seconds"] == pytest.approx(12.5)

    def test_record_zero_elapsed_seconds_is_valid(self):
        """elapsed_seconds=0.0 is falsy but must be used directly, not fall through to ISO
        subtraction."""
        log = DefaultTokenLog()
        log.record(
            "step",
            {"input_tokens": 1, "output_tokens": 1},
            start_ts="2026-01-01T12:00:00+00:00",
            end_ts="2026-01-01T12:00:05+00:00",  # ISO implies 5.0s
            elapsed_seconds=0.0,  # explicit zero — should be used, not skipped
        )
        entries = log.get_report()
        assert entries[0]["elapsed_seconds"] == pytest.approx(0.0)


def _write_session(
    log_root: Path, dir_name: str, tu_data: dict, timestamp: str = "2026-03-07T00:00:00+00:00"
) -> None:
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "token_usage.json").write_text(json.dumps(tu_data))
    index_entry = {"dir_name": dir_name, "timestamp": timestamp, "session_id": dir_name}
    with (log_root / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(index_entry) + "\n")


class TestDefaultTokenLogLoadFromLogDir:
    def test_restores_entries(self, tmp_path):
        """load_from_log_dir populates the store from token_usage.json session files."""
        _write_session(
            tmp_path,
            "s001",
            {
                "step_name": "implement",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 5,
                "timing_seconds": 30.0,
            },
        )
        log = DefaultTokenLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 1
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "implement"
        assert report[0]["input_tokens"] == 100
        assert report[0]["output_tokens"] == 50

    def test_accumulates_into_existing_entries(self, tmp_path):
        """Entries already in the store are summed with loaded data (same step_name merges)."""
        _write_session(
            tmp_path,
            "s001",
            {
                "step_name": "implement",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 0.0,
            },
        )
        log = DefaultTokenLog()
        log.record("implement", {"input_tokens": 200, "output_tokens": 100})
        log.load_from_log_dir(tmp_path)
        report = log.get_report()
        assert report[0]["input_tokens"] == 300
        assert report[0]["output_tokens"] == 150

    def test_since_filter_excludes_old_sessions(self, tmp_path):
        """Sessions with timestamp before since are not loaded."""
        _write_session(
            tmp_path,
            "old",
            {
                "step_name": "implement",
                "input_tokens": 999,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 0.0,
            },
            timestamp="2025-01-01T00:00:00+00:00",
        )
        log = DefaultTokenLog()
        n = log.load_from_log_dir(tmp_path, since="2026-01-01T00:00:00+00:00")
        assert n == 0
        assert log.get_report() == []

    def test_skips_sessions_without_token_file(self, tmp_path):
        """Session dirs missing token_usage.json are skipped without error."""
        session_dir = tmp_path / "sessions" / "no-token"
        session_dir.mkdir(parents=True)
        # No token_usage.json written
        index_entry = {
            "dir_name": "no-token",
            "timestamp": "2026-03-07T00:00:00+00:00",
            "session_id": "no-token",
        }
        (tmp_path / "sessions.jsonl").write_text(json.dumps(index_entry) + "\n")
        log = DefaultTokenLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 0

    def test_returns_count_of_sessions_loaded(self, tmp_path):
        """Return value equals number of session dirs successfully loaded."""
        for i in range(3):
            _write_session(
                tmp_path,
                f"s{i:03d}",
                {
                    "step_name": f"step{i}",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "timing_seconds": 0.0,
                },
            )
        log = DefaultTokenLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 3

    def test_empty_log_root_returns_zero(self, tmp_path):
        """Returns 0 when sessions.jsonl does not exist."""
        log = DefaultTokenLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 0
