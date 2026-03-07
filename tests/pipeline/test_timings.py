"""Tests for autoskillit.pipeline.timings — pipeline step timing."""

from __future__ import annotations

import json
from dataclasses import fields


class TestTimingEntry:
    def test_fields_exist(self):
        from autoskillit.pipeline.timings import TimingEntry

        entry = TimingEntry(step_name="x")
        assert {f.name for f in fields(entry)} == {
            "step_name",
            "total_seconds",
            "invocation_count",
        }

    def test_defaults_are_zero(self):
        from autoskillit.pipeline.timings import TimingEntry

        entry = TimingEntry(step_name="plan")
        assert entry.total_seconds == 0.0
        assert entry.invocation_count == 0

    def test_to_dict_is_json_serializable(self):
        from autoskillit.pipeline.timings import TimingEntry

        entry = TimingEntry(step_name="plan", total_seconds=5.0, invocation_count=1)
        d = entry.to_dict()
        assert json.loads(json.dumps(d)) == d

    def test_to_dict_contains_all_fields(self):
        from autoskillit.pipeline.timings import TimingEntry

        entry = TimingEntry(step_name="implement", total_seconds=3.5, invocation_count=2)
        d = entry.to_dict()
        assert set(d.keys()) == {"step_name", "total_seconds", "invocation_count"}
        assert d["step_name"] == "implement"
        assert d["total_seconds"] == 3.5
        assert d["invocation_count"] == 2


class TestDefaultTimingLog:
    def test_empty_on_init(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        assert log.get_report() == []

    def test_record_single_step(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("plan", 5.0)
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["total_seconds"] == 5.0
        assert report[0]["invocation_count"] == 1

    def test_record_same_step_accumulates(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("plan", 3.0)
        log.record("plan", 3.0)
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["total_seconds"] == 6.0

    def test_invocation_count_increments(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("plan", 1.0)
        log.record("plan", 1.0)
        report = log.get_report()
        assert report[0]["invocation_count"] == 2

    def test_separate_steps_produce_separate_entries(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("a", 1.0)
        log.record("b", 2.0)
        report = log.get_report()
        assert len(report) == 2
        assert report[0]["step_name"] == "a"
        assert report[1]["step_name"] == "b"

    def test_record_is_noop_for_empty_step_name(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("", 5.0)
        assert log.get_report() == []

    def test_negative_duration_clamped_to_zero(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("x", -1.0)
        report = log.get_report()
        assert report[0]["total_seconds"] == 0.0

    def test_get_report_is_defensive_copy(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("plan", 1.0)
        report = log.get_report()
        report.clear()
        assert len(log.get_report()) == 1

    def test_clear_resets_all_entries(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("a", 1.0)
        log.record("b", 2.0)
        log.record("c", 3.0)
        log.clear()
        assert log.get_report() == []

    def test_compute_total_empty_log(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        assert log.compute_total() == {"total_seconds": 0.0}

    def test_compute_total_sums_all_steps(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("a", 10.0)
        log.record("b", 5.0)
        assert log.compute_total()["total_seconds"] == 15.0
