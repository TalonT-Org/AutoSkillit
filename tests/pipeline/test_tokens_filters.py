"""Tests for pipeline.tokens — cwd filter, step name normalization, order/campaign ID scoping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.pipeline.tokens import DefaultTokenLog

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


# ---------------------------------------------------------------------------
# cwd_filter tests
# ---------------------------------------------------------------------------


def _write_session_cwd(
    log_root: Path,
    dir_name: str,
    tu_data: dict,
    cwd: str,
    timestamp: str = "2026-03-07T00:00:00+00:00",
) -> None:
    """Write a session with a cwd field in the sessions.jsonl index."""
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "token_usage.json").write_text(json.dumps(tu_data))
    index_entry = {
        "dir_name": dir_name,
        "timestamp": timestamp,
        "session_id": dir_name,
        "cwd": cwd,
    }
    with (log_root / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(index_entry) + "\n")


class TestLoadFromLogDirCwdFilter:
    """Tests for cwd_filter parameter on DefaultTokenLog.load_from_log_dir."""

    _PLAN_DATA = {
        "step_name": "plan",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "timing_seconds": 10.0,
    }
    _IMPL_DATA = {
        "step_name": "implement",
        "input_tokens": 200,
        "output_tokens": 80,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "timing_seconds": 20.0,
    }

    def test_cwd_filter_isolates_to_matching_cwd(self, tmp_path):
        """cwd_filter loads only sessions whose cwd matches the given path."""
        _write_session_cwd(tmp_path, "sess-A", self._PLAN_DATA, "/runs/pipeline-A")
        _write_session_cwd(tmp_path, "sess-B", self._IMPL_DATA, "/runs/pipeline-B")

        log = DefaultTokenLog()
        count = log.load_from_log_dir(tmp_path, cwd_filter="/runs/pipeline-A")
        assert count == 1, "Should only load the session whose cwd matches"
        steps = log.get_report()
        assert len(steps) == 1
        assert steps[0]["step_name"] == "plan"
        assert steps[0]["input_tokens"] == 100

    def test_cwd_filter_empty_loads_all(self, tmp_path):
        """Empty cwd_filter loads all sessions (backward compatibility)."""
        _write_session_cwd(tmp_path, "sess-A", self._PLAN_DATA, "/runs/pipeline-A")
        _write_session_cwd(tmp_path, "sess-B", self._IMPL_DATA, "/runs/pipeline-B")

        log = DefaultTokenLog()
        count = log.load_from_log_dir(tmp_path, cwd_filter="")
        assert count == 2

    def test_cwd_filter_no_matches_returns_zero(self, tmp_path):
        """cwd_filter that matches nothing returns 0 and leaves the log empty."""
        _write_session_cwd(tmp_path, "sess-A", self._PLAN_DATA, "/runs/pipeline-A")
        _write_session_cwd(tmp_path, "sess-B", self._IMPL_DATA, "/runs/pipeline-B")

        log = DefaultTokenLog()
        count = log.load_from_log_dir(tmp_path, cwd_filter="/runs/nonexistent")
        assert count == 0
        assert log.get_report() == []

    def test_cwd_filter_default_loads_all(self, tmp_path):
        """Calling load_from_log_dir without cwd_filter loads all sessions."""
        _write_session_cwd(tmp_path, "sess-A", self._PLAN_DATA, "/runs/pipeline-A")
        _write_session_cwd(tmp_path, "sess-B", self._IMPL_DATA, "/runs/pipeline-B")

        log = DefaultTokenLog()
        count = log.load_from_log_dir(tmp_path)
        assert count == 2


# ---------------------------------------------------------------------------
# Step name normalization tests
# ---------------------------------------------------------------------------


class TestCanonicalStepName:
    """canonical_step_name() must strip trailing -N suffixes."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("plan", "plan"),
            ("plan-30", "plan"),
            ("implement-31", "implement"),
            ("open_pr-28", "open_pr"),
            ("retry_worktree", "retry_worktree"),  # underscore-separated, no hyphen to strip
            ("audit_impl", "audit_impl"),  # underscore, no hyphen
            ("open-pr", "open-pr"),  # hyphen + non-numeric — not stripped
            ("step-2", "step"),  # two-char suffix stripped
            ("step-123", "step"),  # multi-digit stripped
            ("plan-30-retry", "plan-30-retry"),  # ends non-numeric — not stripped
            ("", ""),  # empty string safe
        ],
    )
    def test_strips_trailing_numeric_suffix(self, raw, expected):
        from autoskillit.pipeline.tokens import canonical_step_name

        assert canonical_step_name(raw) == expected


class TestTokenLogStepNameNormalization:
    def test_suffixed_step_names_aggregate_to_canonical(self):
        """
        record("plan-30", ...) and record("plan-31", ...) must produce
        a single entry keyed by "plan", with invocation_count=2.
        """
        log = DefaultTokenLog()
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

        log.record("plan-30", usage)
        log.record("plan-31", usage)

        report = log.get_report()
        assert len(report) == 1, (
            f"Expected 1 canonical entry, got: {[e['step_name'] for e in report]}"
        )
        entry = report[0]
        assert entry["step_name"] == "plan"
        assert entry["invocation_count"] == 2
        assert entry["input_tokens"] == 200

    def test_canonical_name_and_suffixed_name_merge(self):
        """
        record("plan", ...) and record("plan-30", ...) must merge
        into a single "plan" entry, not two separate entries.
        """
        log = DefaultTokenLog()
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

        log.record("plan", usage)
        log.record("plan-30", usage)

        report = log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["invocation_count"] == 2

    def test_non_numeric_suffix_is_not_stripped(self):
        """
        "open-pr" must not be stripped to "open" — only trailing -N is normalized.
        """
        log = DefaultTokenLog()
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        log.record("open-pr", usage)

        report = log.get_report()
        assert report[0]["step_name"] == "open-pr"


def test_load_from_log_dir_normalizes_suffixed_step_names(tmp_path):
    """
    Sessions written to disk with step_name="plan-30" must be recovered
    under the canonical key "plan", aggregating with other plan-N sessions.
    """
    import json

    log_dir = tmp_path / "logs"
    sessions_dir = log_dir / "sessions"
    cwd = str(tmp_path / "pipeline")
    timestamp = "2026-03-21T00:00:00+00:00"

    for step_name, dir_name in [("plan-30", "s-30"), ("plan-31", "s-31")]:
        session_dir = sessions_dir / dir_name
        session_dir.mkdir(parents=True, exist_ok=True)
        tu_data = {
            "step_name": step_name,
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "timing_seconds": 5.0,
        }
        (session_dir / "token_usage.json").write_text(json.dumps(tu_data))
        index_entry = {"dir_name": dir_name, "timestamp": timestamp, "cwd": cwd}
        with (log_dir / "sessions.jsonl").open("a") as f:
            f.write(json.dumps(index_entry) + "\n")

    log = DefaultTokenLog()
    n = log.load_from_log_dir(str(log_dir), cwd_filter=cwd)

    assert n == 2
    report = log.get_report()
    assert len(report) == 1, f"Expected 1 canonical entry, got: {[e['step_name'] for e in report]}"
    assert report[0]["step_name"] == "plan"
    assert report[0]["invocation_count"] == 2
    assert report[0]["input_tokens"] == 200


class TestOrderIdScoping:
    """Group A: order_id scoping for DefaultTokenLog."""

    def _make_usage(self, **overrides: int) -> dict:
        base = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 5,
        }
        return {**base, **overrides}

    def test_record_with_order_id_creates_scoped_entry(self):
        """A-1: two different order_ids produce separate entries, no cross-contamination."""
        log = DefaultTokenLog()
        log.record("plan", self._make_usage(), order_id="issue-185")
        log.record("plan", self._make_usage(), order_id="issue-186")

        report_185 = log.get_report(order_id="issue-185")
        report_186 = log.get_report(order_id="issue-186")

        assert len(report_185) == 1
        assert len(report_186) == 1
        assert report_185[0]["step_name"] == "plan"
        assert report_186[0]["step_name"] == "plan"
        # No cross-contamination: each order has invocation_count=1
        assert report_185[0]["invocation_count"] == 1
        assert report_186[0]["invocation_count"] == 1

    def test_unfiltered_report_aggregates_across_order_ids(self):
        """A-2: unfiltered report aggregates same step across orders into one entry."""
        log = DefaultTokenLog()
        log.record("plan", self._make_usage(), order_id="issue-185")
        log.record("plan", self._make_usage(), order_id="issue-186")

        # get_report() with no filter returns both entries
        all_entries = log.get_report()
        assert len(all_entries) == 1  # aggregated by step_name → 1 canonical entry
        # But the aggregate invocation_count is 2 (one per order)
        assert all_entries[0]["invocation_count"] == 2

    def test_get_report_order_id_filter_isolates_by_order(self):
        """A-3: get_report(order_id='A') returns only A's entries; B's are absent."""
        log = DefaultTokenLog()
        log.record("plan", self._make_usage(input_tokens=111), order_id="A")
        log.record("implement", self._make_usage(input_tokens=222), order_id="B")

        report_a = log.get_report(order_id="A")
        report_b = log.get_report(order_id="B")

        assert len(report_a) == 1
        assert report_a[0]["step_name"] == "plan"
        assert report_a[0]["input_tokens"] == 111

        assert len(report_b) == 1
        assert report_b[0]["step_name"] == "implement"
        assert report_b[0]["input_tokens"] == 222

    def test_get_report_no_filter_aggregates_all_orders(self):
        """A-4: get_report() with no order_id aggregates across all orders (backward compat)."""
        log = DefaultTokenLog()
        log.record("plan", self._make_usage(input_tokens=100), order_id="A")
        log.record("plan", self._make_usage(input_tokens=200), order_id="B")

        all_entries = log.get_report()
        assert len(all_entries) == 1
        assert all_entries[0]["step_name"] == "plan"
        assert all_entries[0]["input_tokens"] == 300  # 100 + 200
        assert all_entries[0]["invocation_count"] == 2

    def test_compute_total_order_id_filter(self):
        """A-5: compute_total(order_id='issue-185') sums only that order's tokens."""
        log = DefaultTokenLog()
        log.record("plan", self._make_usage(input_tokens=100), order_id="issue-185")
        log.record("plan", self._make_usage(input_tokens=200), order_id="issue-186")

        total_185 = log.compute_total(order_id="issue-185")
        assert total_185["input_tokens"] == 100
        assert total_185["output_tokens"] == 50

    def test_compute_total_no_filter_aggregates_all(self):
        """A-6: compute_total() with no filter aggregates all orders (backward compat)."""
        log = DefaultTokenLog()
        log.record("plan", self._make_usage(input_tokens=100), order_id="issue-185")
        log.record("plan", self._make_usage(input_tokens=200), order_id="issue-186")

        total = log.compute_total()
        assert total["input_tokens"] == 300

    def test_unscoped_record_aggregates_with_no_filter(self):
        """A-7: unscoped record and scoped record both appear in get_report() with no filter."""
        log = DefaultTokenLog()
        log.record("plan", self._make_usage(input_tokens=100))  # no order_id
        log.record("plan", self._make_usage(input_tokens=200), order_id="X")

        all_entries = log.get_report()
        assert len(all_entries) == 1  # same step_name → aggregated
        assert all_entries[0]["invocation_count"] == 2
        assert all_entries[0]["input_tokens"] == 300


# --- Group N: campaign_id_filter tests ---

_TOKEN_DATA = {
    "step_name": "plan",
    "input_tokens": 10,
    "output_tokens": 5,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "timing_seconds": 1.0,
}


def _write_token_session_cid(
    log_root: Path,
    dir_name: str,
    campaign_id: str = "",
    timestamp: str = "2026-04-20T00:00:00+00:00",
) -> None:
    """Write a token_usage session with campaign_id in the sessions.jsonl index."""
    session_dir = log_root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "token_usage.json").write_text(json.dumps(_TOKEN_DATA))
    index_entry = {
        "dir_name": dir_name,
        "timestamp": timestamp,
        "session_id": dir_name,
        "campaign_id": campaign_id,
    }
    with (log_root / "sessions.jsonl").open("a") as f:
        f.write(json.dumps(index_entry) + "\n")


def test_token_load_campaign_id_filter(tmp_path):
    """DefaultTokenLog.load_from_log_dir respects campaign_id_filter."""
    _write_token_session_cid(tmp_path, "c1-a", campaign_id="c1")
    _write_token_session_cid(tmp_path, "c1-b", campaign_id="c1")
    _write_token_session_cid(tmp_path, "c2-a", campaign_id="c2")

    log = DefaultTokenLog()
    n = log.load_from_log_dir(tmp_path, campaign_id_filter="c1")
    assert n == 2

    log2 = DefaultTokenLog()
    n2 = log2.load_from_log_dir(tmp_path, campaign_id_filter="c2")
    assert n2 == 1

    log3 = DefaultTokenLog()
    n3 = log3.load_from_log_dir(tmp_path, campaign_id_filter="")
    assert n3 == 3
