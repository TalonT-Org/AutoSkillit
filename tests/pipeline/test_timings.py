"""Tests for autoskillit.pipeline.timings — pipeline step timing."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest


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


def _write_timing_session(log_root: Path, dir_name: str, st_data: dict, timestamp: str = "2026-03-07T00:00:00+00:00") -> None:
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "step_timing.json").write_text(json.dumps(st_data))
    index_entry = {"dir_name": dir_name, "timestamp": timestamp}
    with (log_root / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(index_entry) + "\n")


class TestDefaultTimingLogLoadFromLogDir:
    def test_restores_timing_entries(self, tmp_path):
        """step_timing.json files in session dirs restore TimingEntry records."""
        from autoskillit.pipeline.timings import DefaultTimingLog

        _write_timing_session(tmp_path, "s001", {"step_name": "implement", "total_seconds": 42.5})
        log = DefaultTimingLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 1
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "implement"
        assert report[0]["total_seconds"] == pytest.approx(42.5)

    def test_since_filter(self, tmp_path):
        """Respects since= timestamp filter (sessions before cutoff excluded)."""
        from autoskillit.pipeline.timings import DefaultTimingLog

        _write_timing_session(tmp_path, "old", {"step_name": "old_step", "total_seconds": 10.0}, timestamp="2025-01-01T00:00:00+00:00")
        log = DefaultTimingLog()
        n = log.load_from_log_dir(tmp_path, since="2026-01-01T00:00:00+00:00")
        assert n == 0
        assert log.get_report() == []

    def test_returns_count(self, tmp_path):
        """Return value equals sessions loaded."""
        from autoskillit.pipeline.timings import DefaultTimingLog

        for i in range(2):
            _write_timing_session(tmp_path, f"s{i:03d}", {"step_name": f"step{i}", "total_seconds": float(i + 1)})
        log = DefaultTimingLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 2
