"""Tests for autoskillit.pipeline.tokens — pipeline token usage tracking."""

from __future__ import annotations

import json
from dataclasses import fields

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
