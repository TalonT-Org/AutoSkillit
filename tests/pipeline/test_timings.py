"""Tests for autoskillit.pipeline.timings — pipeline step timing."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


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


def _write_timing_session(
    log_root: Path, dir_name: str, st_data: dict, timestamp: str = "2026-03-07T00:00:00+00:00"
) -> None:
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "step_timing.json").write_text(json.dumps(st_data))
    index_entry = {"dir_name": dir_name, "timestamp": timestamp}
    with (log_root / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(index_entry) + "\n")


def _write_timing_session_cwd(
    log_root: Path,
    dir_name: str,
    st_data: dict,
    cwd: str,
    timestamp: str = "2026-03-07T00:00:00+00:00",
) -> None:
    """Write a step_timing.json session with cwd in the sessions.jsonl index."""
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "step_timing.json").write_text(json.dumps(st_data))
    index_entry = {"dir_name": dir_name, "timestamp": timestamp, "cwd": cwd}
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

        _write_timing_session(
            tmp_path,
            "old",
            {"step_name": "old_step", "total_seconds": 10.0},
            timestamp="2025-01-01T00:00:00+00:00",
        )
        log = DefaultTimingLog()
        n = log.load_from_log_dir(tmp_path, since="2026-01-01T00:00:00+00:00")
        assert n == 0
        assert log.get_report() == []

    def test_returns_count(self, tmp_path):
        """Return value equals sessions loaded."""
        from autoskillit.pipeline.timings import DefaultTimingLog

        for i in range(2):
            _write_timing_session(
                tmp_path, f"s{i:03d}", {"step_name": f"step{i}", "total_seconds": float(i + 1)}
            )
        log = DefaultTimingLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 2

    def test_load_null_total_seconds(self, tmp_path):
        """TimingLog must handle total_seconds: null without TypeError."""
        from autoskillit.pipeline.timings import DefaultTimingLog

        _write_timing_session(tmp_path, "s001", {"step_name": "implement", "total_seconds": None})
        log = DefaultTimingLog()
        n = log.load_from_log_dir(tmp_path)
        assert n == 1
        report = log.get_report()
        assert report[0]["total_seconds"] == 0.0


class TestLoadFromLogDirCwdFilterTiming:
    """
    DefaultTimingLog.load_from_log_dir() must respect cwd_filter,
    matching the contract already tested for DefaultTokenLog.
    """

    def test_cwd_filter_isolates_to_matching_cwd(self, tmp_path):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        cwd_a = str(tmp_path / "pipeline-a")
        cwd_b = str(tmp_path / "pipeline-b")

        _write_timing_session_cwd(
            log_dir, "s-a", {"step_name": "plan", "total_seconds": 10.0}, cwd_a
        )
        _write_timing_session_cwd(
            log_dir, "s-b", {"step_name": "implement", "total_seconds": 20.0}, cwd_b
        )

        log = DefaultTimingLog()
        n = log.load_from_log_dir(log_dir, cwd_filter=cwd_a)

        assert n == 1
        report = log.get_report()
        step_names = [e["step_name"] for e in report]
        assert "plan" in step_names
        assert "implement" not in step_names

    def test_cwd_filter_empty_loads_all(self, tmp_path):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        for i, cwd in enumerate(["cwd-a", "cwd-b"]):
            _write_timing_session_cwd(
                log_dir,
                f"s-{i}",
                {"step_name": f"step-{i}", "total_seconds": float(i + 1)},
                str(tmp_path / cwd),
            )

        log = DefaultTimingLog()
        n = log.load_from_log_dir(log_dir, cwd_filter="")
        assert n == 2

    def test_cwd_filter_no_matches_returns_zero(self, tmp_path):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        _write_timing_session_cwd(
            log_dir,
            "s-x",
            {"step_name": "plan", "total_seconds": 5.0},
            str(tmp_path / "some-cwd"),
        )

        log = DefaultTimingLog()
        n = log.load_from_log_dir(log_dir, cwd_filter="/nonexistent/pipeline/cwd")
        assert n == 0
        assert log.get_report() == []


# ---------------------------------------------------------------------------
# Step name normalization tests
# ---------------------------------------------------------------------------


class TestTimingLogStepNameNormalization:
    def test_suffixed_step_names_aggregate_to_canonical(self):
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("implement-31", 60.0)
        log.record("implement-32", 55.0)

        report = log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "implement"
        assert report[0]["total_seconds"] == pytest.approx(115.0)

    def test_load_normalizes_suffixed_names(self, tmp_path):
        """Suffixed step names loaded from session files collapse to canonical YAML key."""
        import json

        from autoskillit.pipeline.timings import DefaultTimingLog

        log_dir = tmp_path / "logs"
        sessions_dir = log_dir / "sessions"
        cwd = str(tmp_path / "pipeline")
        timestamp = "2026-03-21T00:00:00+00:00"

        for step_name, dir_name, secs in [
            ("implement-30", "t-30", 60.0),
            ("implement-31", "t-31", 55.0),
        ]:
            session_dir = sessions_dir / dir_name
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / "step_timing.json").write_text(
                json.dumps({"step_name": step_name, "total_seconds": secs})
            )
            index_entry = {"dir_name": dir_name, "timestamp": timestamp, "cwd": cwd}
            with (log_dir / "sessions.jsonl").open("a") as f:
                f.write(json.dumps(index_entry) + "\n")

        log = DefaultTimingLog()
        log.load_from_log_dir(str(log_dir), cwd_filter=cwd)

        report = log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "implement"
        assert report[0]["total_seconds"] == pytest.approx(115.0)


class TestOrderIdScopingTimings:
    """Group B: order_id scoping for DefaultTimingLog."""

    def test_timing_record_with_order_id_creates_scoped_entry(self):
        """B-1: two different order_ids produce separate entries, no cross-contamination."""
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("plan", 10.0, order_id="issue-185")
        log.record("plan", 20.0, order_id="issue-186")

        report_185 = log.get_report(order_id="issue-185")
        report_186 = log.get_report(order_id="issue-186")

        assert len(report_185) == 1
        assert report_185[0]["total_seconds"] == 10.0
        assert report_185[0]["invocation_count"] == 1

        assert len(report_186) == 1
        assert report_186[0]["total_seconds"] == 20.0
        assert report_186[0]["invocation_count"] == 1

    def test_timing_get_report_order_id_filter_isolates(self):
        """B-2: get_report(order_id='A') returns only A's entries; B's are absent."""
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("plan", 5.0, order_id="A")
        log.record("implement", 15.0, order_id="B")

        report_a = log.get_report(order_id="A")
        assert len(report_a) == 1
        assert report_a[0]["step_name"] == "plan"

        report_b = log.get_report(order_id="B")
        assert len(report_b) == 1
        assert report_b[0]["step_name"] == "implement"

    def test_timing_no_filter_aggregates_all_orders(self):
        """B-3: get_report() with no order_id aggregates across all orders (backward compat)."""
        from autoskillit.pipeline.timings import DefaultTimingLog

        log = DefaultTimingLog()
        log.record("plan", 5.0, order_id="A")
        log.record("plan", 10.0, order_id="B")

        all_entries = log.get_report()
        assert len(all_entries) == 1
        assert all_entries[0]["step_name"] == "plan"
        assert all_entries[0]["total_seconds"] == 15.0
        assert all_entries[0]["invocation_count"] == 2


# --- Group N: campaign_id_filter tests ---


def _write_timing_session_cid(
    log_root: Path,
    dir_name: str,
    campaign_id: str = "",
    timestamp: str = "2026-04-20T00:00:00+00:00",
) -> None:
    """Write a step_timing session with campaign_id in the sessions.jsonl index."""
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "step_timing.json").write_text(
        json.dumps({"step_name": "implement", "total_seconds": 10.0})
    )
    index_entry = {
        "dir_name": dir_name,
        "timestamp": timestamp,
        "campaign_id": campaign_id,
    }
    with (log_root / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(index_entry) + "\n")


def test_timing_load_campaign_id_filter(tmp_path):
    """DefaultTimingLog.load_from_log_dir respects campaign_id_filter."""
    from autoskillit.pipeline.timings import DefaultTimingLog

    _write_timing_session_cid(tmp_path, "c1-a", campaign_id="c1")
    _write_timing_session_cid(tmp_path, "c1-b", campaign_id="c1")
    _write_timing_session_cid(tmp_path, "c2-a", campaign_id="c2")

    log = DefaultTimingLog()
    n = log.load_from_log_dir(tmp_path, campaign_id_filter="c1")
    assert n == 2

    log2 = DefaultTimingLog()
    n2 = log2.load_from_log_dir(tmp_path, campaign_id_filter="c2")
    assert n2 == 1

    log3 = DefaultTimingLog()
    n3 = log3.load_from_log_dir(tmp_path, campaign_id_filter="")
    assert n3 == 3
