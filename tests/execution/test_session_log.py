"""Tests for the session diagnostics log writer."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autoskillit.execution.linux_tracing import read_boot_id, read_starttime_ticks
from autoskillit.execution.session_log import (
    flush_session_log,
    read_telemetry_clear_marker,
    recover_crashed_sessions,
    resolve_log_dir,
    write_telemetry_clear_marker,
)
from autoskillit.fleet import build_protected_campaign_ids

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _snap(
    *,
    captured_at: str = "2026-03-03T12:00:00+00:00",
    vm_rss_kb: int = 100000,
    oom_score: int = 50,
    fd_count: int = 10,
    fd_soft_limit: int = 1024,
    state: str = "sleeping",
) -> dict[str, object]:
    return {
        "captured_at": captured_at,
        "state": state,
        "vm_rss_kb": vm_rss_kb,
        "oom_score": oom_score,
        "fd_count": fd_count,
        "fd_soft_limit": fd_soft_limit,
        "sig_pnd": "0000000000000000",
        "sig_blk": "0000000000000000",
        "sig_cgt": "0000000000000000",
        "threads": 4,
        "wchan": "",
        "ctx_switches_voluntary": 500,
        "ctx_switches_involuntary": 20,
    }


def _flush(tmp_path: Path, **overrides) -> None:
    defaults = {
        "log_dir": str(tmp_path),
        "cwd": "/home/test/project",
        "session_id": "test-session-001",
        "pid": 12345,
        "skill_command": "/autoskillit:investigate some error",
        "success": True,
        "subtype": "completed",
        "exit_code": 0,
        "start_ts": "2026-03-03T12:00:00+00:00",
        "proc_snapshots": [_snap(), _snap(), _snap()],
    }
    defaults.update(overrides)
    flush_session_log(**defaults)


def test_flush_session_log_creates_directory_structure(tmp_path):
    """Flush creates the expected directory structure."""
    _flush(tmp_path)
    session_dir = tmp_path / "sessions" / "test-session-001"
    assert session_dir.is_dir()
    assert (session_dir / "proc_trace.jsonl").is_file()
    assert (session_dir / "summary.json").is_file()
    assert (tmp_path / "sessions.jsonl").is_file()


def test_flush_session_log_writes_proc_trace_as_jsonl(tmp_path):
    """proc_trace.jsonl has exactly 3 lines, each valid JSON."""
    _flush(tmp_path)
    trace_path = tmp_path / "sessions" / "test-session-001" / "proc_trace.jsonl"
    lines = trace_path.read_text().strip().split("\n")
    assert len(lines) == 3
    for i, line in enumerate(lines):
        record = json.loads(line)
        assert record["seq"] == i
        assert record["event"] == "snapshot"
        assert record["pid"] == 12345
        assert "vm_rss_kb" in record


def test_flush_session_log_writes_summary_json(tmp_path):
    """summary.json contains expected session metadata."""
    _flush(tmp_path)
    summary_path = tmp_path / "sessions" / "test-session-001" / "summary.json"
    summary = json.loads(summary_path.read_text())
    assert summary["session_id"] == "test-session-001"
    assert summary["pid"] == 12345
    assert summary["success"] is True
    assert summary["snapshot_count"] == 3
    assert summary["cwd"] == "/home/test/project"
    assert "peak_rss_kb" in summary
    assert "peak_oom_score" in summary


def test_flush_session_log_creates_anomalies_file_only_when_anomalies_exist(tmp_path):
    """anomalies.jsonl is only created when anomalies are detected."""
    # Normal snapshots — no anomalies
    _flush(tmp_path, session_id="normal-session")
    anomalies_path = tmp_path / "sessions" / "normal-session" / "anomalies.jsonl"
    assert not anomalies_path.exists()

    # Snapshots with OOM critical — should create anomalies
    _flush(
        tmp_path,
        session_id="anomaly-session",
        proc_snapshots=[_snap(oom_score=900)],
    )
    anomalies_path = tmp_path / "sessions" / "anomaly-session" / "anomalies.jsonl"
    assert anomalies_path.is_file()
    lines = anomalies_path.read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "oom_critical"


def test_flush_session_log_appends_to_index(tmp_path):
    """Two flushes produce exactly 2 lines in sessions.jsonl."""
    _flush(tmp_path, session_id="session-a")
    _flush(tmp_path, session_id="session-b")
    index_path = tmp_path / "sessions.jsonl"
    lines = [ln for ln in index_path.read_text().strip().split("\n") if ln.strip()]
    assert len(lines) == 2
    a = json.loads(lines[0])
    b = json.loads(lines[1])
    assert a["session_id"] == "session-a"
    assert b["session_id"] == "session-b"


def test_flush_session_log_fallback_dirname_when_no_session_id(tmp_path):
    """Empty session_id falls back to no_session_ prefix with timestamp."""
    _flush(tmp_path, session_id="", pid=99999, start_ts="2026-03-03T12:00:00+00:00")
    sessions_dir = tmp_path / "sessions"
    dirs = list(sessions_dir.iterdir())
    assert len(dirs) == 1
    dir_name = dirs[0].name
    assert dir_name.startswith("no_session_")
    assert "2026" in dir_name


def test_flush_session_log_uses_resolved_session_id(tmp_path):
    """flush_session_log uses the caller-resolved session_id for dir name."""
    _flush(tmp_path, session_id="chan-b-uuid-123")
    session_dir = tmp_path / "sessions" / "chan-b-uuid-123"
    assert session_dir.is_dir()
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["session_id"] == "chan-b-uuid-123"


def test_flush_summary_includes_claude_code_log_for_real_session(tmp_path):
    """Flush with a real session_id produces claude_code_log in summary.json."""
    _flush(tmp_path, session_id="real-session-abc", cwd="/home/test/project")
    summary = json.loads((tmp_path / "sessions" / "real-session-abc" / "summary.json").read_text())
    expected = str(
        Path.home() / ".claude" / "projects" / "-home-test-project" / "real-session-abc.jsonl"
    )
    assert summary["claude_code_log"] == expected


def test_flush_summary_claude_code_log_null_for_fallback_session(tmp_path):
    """Flush with empty session_id produces claude_code_log: null in summary.json."""
    _flush(tmp_path, session_id="")
    sessions_dir = tmp_path / "sessions"
    session_dir = next(sessions_dir.iterdir())
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["claude_code_log"] is None


def test_flush_index_includes_claude_code_log(tmp_path):
    """sessions.jsonl index entry includes claude_code_log for real sessions."""
    _flush(tmp_path, session_id="idx-session-xyz", cwd="/home/test/project")
    index_path = tmp_path / "sessions.jsonl"
    entry = json.loads(index_path.read_text().strip().split("\n")[-1])
    expected = str(
        Path.home() / ".claude" / "projects" / "-home-test-project" / "idx-session-xyz.jsonl"
    )
    assert entry["claude_code_log"] == expected


def test_flush_index_claude_code_log_null_for_fallback(tmp_path):
    """sessions.jsonl index entry has claude_code_log: null for fallback sessions."""
    _flush(tmp_path, session_id="")
    index_path = tmp_path / "sessions.jsonl"
    entry = json.loads(index_path.read_text().strip().split("\n")[-1])
    assert entry["claude_code_log"] is None


def test_flush_session_log_handles_empty_snapshots(tmp_path):
    """proc_snapshots=None produces no proc_trace.jsonl but summary is still written."""
    _flush(tmp_path, proc_snapshots=None)
    session_dir = tmp_path / "sessions" / "test-session-001"
    assert not (session_dir / "proc_trace.jsonl").exists()
    assert (session_dir / "summary.json").is_file()
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["snapshot_count"] == 0


def test_flush_session_log_retention_purges_oldest(tmp_path):
    """After 503 sessions, only 500 remain and the 3 oldest are gone."""

    # Create 502 session directories with staggered mtimes
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(502):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        (d / "summary.json").write_text("{}")
        # Set mtime to ensure ordering
        mtime = 1000000000 + i
        import os

        os.utime(d, (mtime, mtime))
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")

    # Flush a 503rd session
    _flush(tmp_path, session_id="session-0502")

    remaining = list(sessions_dir.iterdir())
    assert len(remaining) == 500

    # The 3 oldest (session-0000, session-0001, session-0002) should be gone
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()
    assert not (sessions_dir / "session-0002").exists()

    # The newest should still be there
    assert (sessions_dir / "session-0502").exists()

    # Index should only contain entries for surviving sessions
    index_lines = [ln for ln in index_path.read_text().strip().split("\n") if ln.strip()]
    assert len(index_lines) == 500


# --- resolve_log_dir tests ---


def test_resolve_log_dir_default_linux(monkeypatch):
    """Empty log_dir on Linux (no XDG_DATA_HOME) uses ~/.local/share/autoskillit/logs."""
    monkeypatch.setattr("autoskillit.execution.session_log.sys.platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = resolve_log_dir("")
    assert result == Path.home() / ".local" / "share" / "autoskillit" / "logs"


def test_resolve_log_dir_xdg_override(monkeypatch):
    """XDG_DATA_HOME override is respected."""
    monkeypatch.setattr("autoskillit.execution.session_log.sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/xdg")
    result = resolve_log_dir("")
    assert result == Path("/custom/xdg/autoskillit/logs")


def test_resolve_log_dir_explicit_override():
    """Explicit log_dir is used as-is."""
    result = resolve_log_dir("/custom/path")
    assert result == Path("/custom/path")


def test_proc_trace_timestamps_are_per_snapshot_not_session_start(tmp_path):
    """Each snapshot record must carry its own captured_at time, not the session start."""
    ts1 = "2026-03-03T12:00:00+00:00"
    ts2 = "2026-03-03T12:00:05+00:00"
    ts3 = "2026-03-03T12:00:10+00:00"
    snaps = [
        _snap(captured_at=ts1),
        _snap(captured_at=ts2),
        _snap(captured_at=ts3),
    ]
    _flush(tmp_path, proc_snapshots=snaps, start_ts="2026-03-03T12:00:00+00:00")

    session_dir = tmp_path / "sessions" / "test-session-001"
    records = [
        json.loads(line) for line in (session_dir / "proc_trace.jsonl").read_text().splitlines()
    ]
    assert records[0]["ts"] == ts1
    assert records[1]["ts"] == ts2
    assert records[2]["ts"] == ts3
    # All three must differ — not the session start repeated
    assert len({r["ts"] for r in records}) == 3


def test_summary_contains_temporal_completion_fields(tmp_path):
    """summary.json must record when the session ended and how long it ran."""
    _flush(
        tmp_path,
        start_ts="2026-03-03T12:00:00+00:00",
        end_ts="2026-03-03T12:05:00+00:00",
        termination_reason="completed",
        snapshot_interval_seconds=5.0,
    )
    session_dir = tmp_path / "sessions" / "test-session-001"
    summary = json.loads((session_dir / "summary.json").read_text())

    assert summary["end_ts"] == "2026-03-03T12:05:00+00:00"
    assert summary["duration_seconds"] == pytest.approx(300.0)
    assert summary["termination_reason"] == "completed"
    assert summary["snapshot_interval_seconds"] == 5.0


# --- termination_reason tests ---


def test_flush_includes_termination_reason(tmp_path):
    """summary.json includes termination_reason when provided."""
    _flush(tmp_path, session_id="crash-session", termination_reason="CRASHED")
    session_dir = tmp_path / "sessions" / "crash-session"
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["termination_reason"] == "CRASHED"


def test_flush_termination_reason_defaults_to_empty(tmp_path):
    """summary.json has termination_reason = '' when not provided."""
    _flush(tmp_path, session_id="normal-session")
    session_dir = tmp_path / "sessions" / "normal-session"
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["termination_reason"] == ""


def test_proc_trace_uses_snapshot_captured_at(tmp_path):
    """proc_trace.jsonl ts field equals snapshot captured_at, not start_ts."""
    snap = dict(_snap(), captured_at="2026-03-03T10:00:00+00:00")
    _flush(
        tmp_path,
        session_id="ts-test",
        proc_snapshots=[snap],
        start_ts="2026-03-03T09:00:00+00:00",
    )
    trace_path = tmp_path / "sessions" / "ts-test" / "proc_trace.jsonl"
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert records[0]["ts"] == "2026-03-03T10:00:00+00:00"


def test_proc_trace_falls_back_to_start_ts_when_no_captured_at(tmp_path):
    """proc_trace.jsonl ts falls back to start_ts when captured_at is absent."""
    snap = {k: v for k, v in _snap().items() if k != "captured_at"}  # no captured_at key
    _flush(
        tmp_path,
        session_id="fallback-ts",
        proc_snapshots=[snap],
        start_ts="2026-03-03T09:00:00+00:00",
    )
    trace_path = tmp_path / "sessions" / "fallback-ts" / "proc_trace.jsonl"
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert records[0]["ts"] == "2026-03-03T09:00:00+00:00"


# --- recover_crashed_sessions tests ---


def test_recover_crashed_sessions_noop_when_no_orphans(tmp_path):
    """Returns 0 when tmpfs is empty."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert count == 0


def test_recover_crashed_sessions_noop_when_tmpfs_missing(tmp_path):
    """Returns 0 when tmpfs_path does not exist."""
    count = recover_crashed_sessions(
        tmpfs_path=str(tmp_path / "nonexistent"), log_dir=str(tmp_path / "logs")
    )
    assert count == 0


def test_recover_crashed_sessions_skips_recent_files(tmp_path):
    """Files modified within the last 30 seconds are skipped (may be active)."""

    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    trace = tmpfs / "autoskillit_trace_12345.jsonl"
    trace.write_text(
        json.dumps({"vm_rss_kb": 500, "captured_at": "2026-03-03T10:00:00+00:00"}) + "\n"
    )
    # Leave the file fresh (mtime = now)
    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert count == 0


def _write_old_trace(tmpfs: Path, filename: str, content: str) -> Path:
    """Write a trace file (backdated 60s) and its enrollment sidecar.

    The enrollment sidecar uses the current boot_id so Gate 2 passes.
    The PID embedded in the filename is expected to be dead (so Gate 3 passes).
    """
    trace = tmpfs / filename
    trace.write_text(content)
    old_mtime = time.time() - 60
    os.utime(trace, (old_mtime, old_mtime))

    # Write companion enrollment sidecar so Gate 1 passes
    try:
        pid = int(Path(filename).stem.split("_")[-1])
    except (ValueError, IndexError):
        pid = 0
    enrollment = tmpfs / f"autoskillit_enrollment_{pid}.json"
    enrollment.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": pid,
                "boot_id": read_boot_id() or "",
                "starttime_ticks": None,
                "session_id": "",
                "enrolled_at": "2026-01-01T00:00:00+00:00",
                "kitchen_id": "",
                "order_id": "",
            }
        )
    )
    return trace


def test_recover_crashed_sessions_finalizes_orphaned_file(tmp_path):
    """recover_crashed_sessions reads tmpfs file and writes permanent session dir."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    _write_old_trace(
        tmpfs,
        "autoskillit_trace_12345.jsonl",
        json.dumps({"vm_rss_kb": 500, "captured_at": "2026-03-03T10:00:00+00:00"}) + "\n",
    )
    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert count == 1
    sessions = list((tmp_path / "logs" / "sessions").iterdir())
    assert len(sessions) == 1
    assert "crashed" in sessions[0].name
    assert (sessions[0] / "summary.json").exists()
    summary = json.loads((sessions[0] / "summary.json").read_text())
    assert summary["termination_reason"] == "CRASHED"
    assert summary["success"] is False


def test_recover_crashed_sessions_deletes_tmpfs_file(tmp_path):
    """Trace file is removed from tmpfs after recovery."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    trace = _write_old_trace(
        tmpfs,
        "autoskillit_trace_99999.jsonl",
        json.dumps({"vm_rss_kb": 300, "captured_at": "2026-03-03T10:00:00+00:00"}) + "\n",
    )
    recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert not trace.exists()


def test_recover_crashed_sessions_handles_multiple_files(tmp_path):
    """Multiple orphaned trace files are all recovered."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    for pid in [111, 222, 333]:
        _write_old_trace(
            tmpfs,
            f"autoskillit_trace_{pid}.jsonl",
            json.dumps({"vm_rss_kb": 100, "captured_at": "2026-03-03T10:00:00+00:00"}) + "\n",
        )
    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path / "logs"))
    assert count == 3
    sessions = list((tmp_path / "logs" / "sessions").iterdir())
    assert len(sessions) == 3


def test_flush_session_log_backward_clock_produces_non_negative_duration(tmp_path):
    """duration_seconds must never be negative, even if end_ts precedes start_ts."""
    start_ts = "2026-01-01T12:05:00+00:00"  # later
    end_ts = "2026-01-01T12:00:00+00:00"  # earlier — backward clock
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="backward-clock-test",
        pid=1,
        skill_command="/test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts=start_ts,
        end_ts=end_ts,
        proc_snapshots=[],
        termination_reason="completed",
        snapshot_interval_seconds=5.0,
    )
    session_dir = tmp_path / "sessions" / "backward-clock-test"
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["duration_seconds"] >= 0, (
        f"duration_seconds must not be negative, got {summary['duration_seconds']}"
    )


def test_flush_session_log_uses_elapsed_seconds_over_iso_subtraction(tmp_path):
    """When elapsed_seconds is provided, it is used as duration_seconds, not ISO subtraction."""
    start_ts = "2026-01-01T12:00:00+00:00"
    end_ts = "2026-01-01T12:00:05+00:00"  # ISO implies 5.0s
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="elapsed-seconds-test",
        pid=1,
        skill_command="/test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts=start_ts,
        end_ts=end_ts,
        elapsed_seconds=12.5,  # monotonic says 12.5s
        proc_snapshots=[],
        termination_reason="completed",
        snapshot_interval_seconds=5.0,
    )
    session_dir = tmp_path / "sessions" / "elapsed-seconds-test"
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["duration_seconds"] == pytest.approx(12.5), (
        "elapsed_seconds param must override ISO subtraction"
    )


def test_flush_session_log_zero_elapsed_seconds_is_valid(tmp_path):
    """elapsed_seconds=0.0 is falsy but must be used as duration_seconds.

    Must not fall through to ISO subtraction when elapsed_seconds is 0.0.
    """
    start_ts = "2026-01-01T12:00:00+00:00"
    end_ts = "2026-01-01T12:00:05+00:00"  # ISO implies 5.0s
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="zero-elapsed-test",
        pid=1,
        skill_command="/test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts=start_ts,
        end_ts=end_ts,
        elapsed_seconds=0.0,  # explicit zero — must not fall through to ISO subtraction
        proc_snapshots=[],
        termination_reason="completed",
        snapshot_interval_seconds=5.0,
    )
    session_dir = tmp_path / "sessions" / "zero-elapsed-test"
    summary = json.loads((session_dir / "summary.json").read_text())
    assert summary["duration_seconds"] == 0.0


# --- telemetry persistence tests ---


def test_flush_writes_token_usage_json_when_step_provided(tmp_path):
    """token_usage.json appears in session dir when step_name + token_usage given."""
    _flush(
        tmp_path,
        step_name="implement",
        token_usage={"input_tokens": 100, "output_tokens": 50},
        proc_snapshots=None,
        success=False,
    )
    session_dir = tmp_path / "sessions" / "test-session-001"
    assert (session_dir / "token_usage.json").is_file()


def test_flush_omits_token_usage_json_when_no_step_name(tmp_path):
    """token_usage.json is NOT written when step_name is empty, even if token_usage provided."""
    _flush(
        tmp_path,
        step_name="",
        token_usage={"input_tokens": 100},
        proc_snapshots=None,
        success=False,
    )
    session_dir = tmp_path / "sessions" / "test-session-001"
    assert not (session_dir / "token_usage.json").exists()


def test_flush_writes_step_timing_json(tmp_path):
    """step_timing.json appears when step_name and timing_seconds > 0 provided."""
    _flush(
        tmp_path, step_name="implement", timing_seconds=42.5, proc_snapshots=None, success=False
    )
    session_dir = tmp_path / "sessions" / "test-session-001"
    assert (session_dir / "step_timing.json").is_file()


# ---------------------------------------------------------------------------
# Test 1.10 — proc_trace.jsonl rows self-identify with comm field
# ---------------------------------------------------------------------------


def test_proc_trace_jsonl_rows_include_comm(tmp_path):
    """proc_trace.jsonl rows must include a 'comm' field for post-hoc drift detection.

    Test 1.10: after flush_session_log, every row in proc_trace.jsonl must carry
    the process identity (comm). This makes any drift visible to anyone triaging
    a session log — any row lacking comm is a drift indicator.
    """
    snaps = [
        {**_snap(), "comm": "claude"},
        {**_snap(), "comm": "claude"},
        {**_snap(), "comm": "claude"},
    ]
    _flush(tmp_path, proc_snapshots=snaps, session_id="comm-test-001")

    trace_path = tmp_path / "sessions" / "comm-test-001" / "proc_trace.jsonl"
    assert trace_path.exists()
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    for i, row in enumerate(rows):
        assert "comm" in row, (
            f"Row {i} in proc_trace.jsonl is missing 'comm' field. "
            "Every snapshot row must self-identify the traced process."
        )
        assert row["comm"] == "claude", f"Expected comm='claude' in row {i}, got {row['comm']!r}"


# ---------------------------------------------------------------------------
# Test 1.11 — recovery path reads comm and excludes alien files
# ---------------------------------------------------------------------------


def _write_old_trace_with_comm(tmpfs: Path, pid: int, comm: str, *, n_snaps: int = 2) -> Path:
    """Write a backdated trace file with snapshots that have a specific comm."""
    filename = f"autoskillit_trace_{pid}.jsonl"
    trace = tmpfs / filename
    snaps = []
    for _ in range(n_snaps):
        snap_dict = {**_snap(), "comm": comm}
        snaps.append(json.dumps(snap_dict))
    trace.write_text("\n".join(snaps) + "\n")

    # Backdate so Gate 1 (age > 30s) passes
    old_mtime = time.time() - 60
    os.utime(trace, (old_mtime, old_mtime))

    # Write enrollment sidecar so Gate 1 (sidecar present) passes.
    # Use schema_version=2 with comm='claude' — autoskillit always enrolls its own
    # binary as 'claude'. Snapshots whose first comm != enrollment.comm are alien.
    enrollment = tmpfs / f"autoskillit_enrollment_{pid}.json"
    enrollment.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pid": pid,
                "boot_id": read_boot_id() or "",
                "starttime_ticks": None,
                "session_id": "",
                "enrolled_at": "2026-01-01T00:00:00+00:00",
                "kitchen_id": "",
                "order_id": "",
                "comm": "claude",
            }
        )
    )
    return trace


def test_recover_crashed_sessions_excludes_non_claude_trace_files(tmp_path):
    """recover_crashed_sessions tags alien trace files (non-claude comm) and excludes them.

    Test 1.11: place two trace files — one with comm='claude' and one with
    comm='sleep'. The 'sleep' file is an alien artifact (test pollution or a
    non-autoskillit process). After the fix, recovery recognises it via comm
    and either skips it or marks it with alien=true in the recovered record.
    """
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    log_dir = tmp_path / "logs"

    # One legitimate claude trace
    _write_old_trace_with_comm(tmpfs, pid=20001, comm="claude")
    # One alien trace (e.g., leftover from a test or wrong process)
    _write_old_trace_with_comm(tmpfs, pid=20002, comm="sleep")

    recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(log_dir))

    sessions_dir = log_dir / "sessions"
    recovered = list(sessions_dir.iterdir()) if sessions_dir.exists() else []
    session_summaries = []
    for s in recovered:
        summary_file = s / "summary.json"
        if summary_file.exists():
            session_summaries.append(json.loads(summary_file.read_text()))

    claude_sessions = [s for s in session_summaries if "20001" in s.get("session_id", "")]

    # The claude trace must be recovered (not excluded)
    assert claude_sessions, "The claude trace file must be recovered by recover_crashed_sessions"

    # The alien trace should not produce a normal session — it should be excluded
    # or marked as alien so it doesn't pollute capacity planning / anomaly analytics
    alien_included_normally = [
        s
        for s in session_summaries
        if "20002" in s.get("session_id", "") and not s.get("alien") and not s.get("pre_fix_data")
    ]
    assert not alien_included_normally, (
        "Alien trace (comm='sleep') must not be recovered as a normal session. "
        "It should be skipped or marked alien=true to prevent #771-style mis-attribution."
    )


def test_flush_writes_audit_log_json(tmp_path):
    """audit_log.json written to session dir when step_name and audit_record dict provided."""
    record = {
        "timestamp": "2026-01-01T00:00:00Z",
        "skill_command": "/foo",
        "exit_code": 1,
        "subtype": "error",
        "needs_retry": False,
        "retry_reason": "none",
        "stderr": "oops",
    }
    _flush(
        tmp_path, step_name="implement", audit_record=record, proc_snapshots=None, success=False
    )
    session_dir = tmp_path / "sessions" / "test-session-001"
    assert (session_dir / "audit_log.json").is_file()


def test_flush_omits_audit_log_when_no_record(tmp_path):
    """audit_log.json NOT written when audit_record=None."""
    _flush(tmp_path, audit_record=None, proc_snapshots=None, step_name="implement", success=False)
    session_dir = tmp_path / "sessions" / "test-session-001"
    assert not (session_dir / "audit_log.json").exists()


def test_flush_index_includes_step_name_and_token_fields(tmp_path):
    """sessions.jsonl entry has step_name, input_tokens, output_tokens fields."""
    _flush(
        tmp_path,
        step_name="implement",
        token_usage={"input_tokens": 100, "output_tokens": 50},
        proc_snapshots=None,
        success=False,
    )
    lines = (tmp_path / "sessions.jsonl").read_text().strip().split("\n")
    entry = json.loads(lines[-1])
    assert entry["step_name"] == "implement"
    assert entry["input_tokens"] == 100
    assert entry["output_tokens"] == 50


def test_flush_index_token_fields_zero_when_no_step(tmp_path):
    """sessions.jsonl entry has step_name='' and token fields=0 when no step telemetry."""
    _flush(tmp_path, proc_snapshots=None, success=False)  # no step_name
    lines = (tmp_path / "sessions.jsonl").read_text().strip().split("\n")
    entry = json.loads(lines[-1])
    assert entry["step_name"] == ""
    assert entry["input_tokens"] == 0
    assert entry["output_tokens"] == 0


def test_token_usage_json_schema(tmp_path):
    """token_usage.json contains all expected fields."""
    _flush(
        tmp_path,
        step_name="plan",
        token_usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 1,
        },
        timing_seconds=15.0,
        proc_snapshots=None,
        success=False,
    )
    tu = json.loads((tmp_path / "sessions" / "test-session-001" / "token_usage.json").read_text())
    assert tu["step_name"] == "plan"
    assert tu["input_tokens"] == 10
    assert tu["output_tokens"] == 5
    assert tu["cache_creation_input_tokens"] == 2
    assert tu["cache_read_input_tokens"] == 1
    assert tu["timing_seconds"] == 15.0


def test_step_timing_json_schema(tmp_path):
    """step_timing.json contains step_name and total_seconds."""
    _flush(tmp_path, step_name="plan", timing_seconds=20.0, proc_snapshots=None, success=False)
    st = json.loads((tmp_path / "sessions" / "test-session-001" / "step_timing.json").read_text())
    assert st["step_name"] == "plan"
    assert st["total_seconds"] == 20.0


def test_audit_log_json_schema(tmp_path):
    """audit_log.json contains list with expected failure record fields."""
    record = {
        "timestamp": "2026-01-01T00:00:00Z",
        "skill_command": "/foo",
        "exit_code": 1,
        "subtype": "error",
        "needs_retry": False,
        "retry_reason": "none",
        "stderr": "bad",
    }
    _flush(
        tmp_path, step_name="implement", audit_record=record, proc_snapshots=None, success=False
    )
    al = json.loads((tmp_path / "sessions" / "test-session-001" / "audit_log.json").read_text())
    assert isinstance(al, list)
    assert len(al) == 1
    assert al[0]["skill_command"] == "/foo"
    assert al[0]["exit_code"] == 1


# Clear marker tests


def test_write_read_clear_marker_roundtrip(tmp_path):
    before = datetime.now(UTC)
    write_telemetry_clear_marker(tmp_path)
    after = datetime.now(UTC)
    result = read_telemetry_clear_marker(tmp_path)
    assert result is not None
    assert before <= result <= after


def test_read_clear_marker_missing_returns_none(tmp_path):
    assert read_telemetry_clear_marker(tmp_path) is None


def test_read_clear_marker_corrupt_returns_none(tmp_path):
    (tmp_path / ".telemetry_cleared_at").write_text("not-a-date")
    assert read_telemetry_clear_marker(tmp_path) is None


def test_write_clear_marker_is_atomic(tmp_path):
    # Calling write twice does not corrupt — second write wins
    write_telemetry_clear_marker(tmp_path)
    t1 = read_telemetry_clear_marker(tmp_path)
    write_telemetry_clear_marker(tmp_path)
    t2 = read_telemetry_clear_marker(tmp_path)
    assert t1 is not None
    assert t2 is not None
    assert t2 >= t1


def test_flush_session_log_includes_write_path_warnings_in_summary(tmp_path):
    """summary.json records write_path_warnings list."""
    warnings = [
        "Write tool wrote to /source/repo/.autoskillit/temp/foo.md (outside cwd /clone)",
        "Edit tool wrote to /source/repo/src/file.py (outside cwd /clone)",
    ]
    _flush(tmp_path, session_id="warn-session", write_path_warnings=warnings, proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "warn-session" / "summary.json").read_text())
    assert summary["write_path_warnings"] == warnings


def test_flush_session_log_empty_warnings_produce_empty_list(tmp_path):
    """No warnings → write_path_warnings is [] in summary."""
    _flush(tmp_path, session_id="clean-session", write_path_warnings=[], proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "clean-session" / "summary.json").read_text())
    assert summary["write_path_warnings"] == []


def test_flush_session_log_none_warnings_treated_as_empty(tmp_path):
    """write_path_warnings=None (default) produces empty list in summary."""
    _flush(tmp_path, session_id="default-warn", proc_snapshots=None)  # no write_path_warnings arg
    summary = json.loads((tmp_path / "sessions" / "default-warn" / "summary.json").read_text())
    assert summary["write_path_warnings"] == []


def test_flush_session_log_includes_write_call_count_in_summary(tmp_path):
    """summary.json records write_call_count."""
    _flush(tmp_path, session_id="wc-session", write_call_count=5, proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "wc-session" / "summary.json").read_text())
    assert summary["write_call_count"] == 5


def test_flush_session_log_write_call_count_in_index(tmp_path):
    """sessions.jsonl index includes write_call_count."""
    _flush(tmp_path, session_id="wc-idx", write_call_count=3, proc_snapshots=None)
    index_path = tmp_path / "sessions.jsonl"
    entry = json.loads(index_path.read_text().strip().split("\n")[-1])
    assert entry["write_call_count"] == 3


def test_flush_session_log_write_call_count_defaults_to_zero(tmp_path):
    """write_call_count defaults to 0 when not specified."""
    _flush(tmp_path, session_id="wc-default", proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "wc-default" / "summary.json").read_text())
    assert summary["write_call_count"] == 0


def test_flush_session_log_writes_kitchen_id(tmp_path):
    """kitchen_id parameter is written to sessions.jsonl index entry."""
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/worktree",
        kitchen_id="my-pipeline-123",
        session_id="sess-001",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-03-27T08:00:00",
        proc_snapshots=None,
    )

    index = (tmp_path / "sessions.jsonl").read_text()
    entry = json.loads(index.strip())
    assert entry["kitchen_id"] == "my-pipeline-123"


def test_flush_session_log_writes_order_id_to_index(tmp_path):
    """order_id is written to sessions.jsonl index entry when provided."""
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/worktree",
        kitchen_id="kitchen-abc",
        order_id="issue-185",
        session_id="sess-002",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-03-27T08:00:00",
        proc_snapshots=None,
    )

    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert entry["order_id"] == "issue-185"


def test_flush_session_log_order_id_defaults_to_empty(tmp_path):
    """order_id defaults to empty string when not supplied."""
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/worktree",
        kitchen_id="kitchen-abc",
        session_id="sess-003",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-03-27T08:00:00",
        proc_snapshots=None,
    )

    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert "order_id" in entry
    assert entry["order_id"] == ""


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: uses /proc and boot_id")
def test_recover_crashed_sessions_skips_live_pid(tmp_path):
    """A trace file whose enrolled PID is still alive must not be recovered."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    pid = os.getpid()
    trace = tmpfs / f"autoskillit_trace_{pid}.jsonl"
    enrollment = tmpfs / f"autoskillit_enrollment_{pid}.json"
    trace.write_text("")
    enrollment.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": pid,
                "boot_id": read_boot_id() or "",
                "starttime_ticks": read_starttime_ticks(pid),
                "session_id": "",
                "enrolled_at": datetime.now(UTC).isoformat(),
                "kitchen_id": "",
                "order_id": "",
            }
        )
    )
    os.utime(trace, (time.time() - 60,) * 2)

    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path))

    assert count == 0
    assert trace.exists(), "Trace file for alive PID must not be deleted"
    assert enrollment.exists(), "Enrollment sidecar for alive PID must not be deleted"


def test_recover_crashed_sessions_skips_file_without_enrollment(tmp_path):
    """A trace file with no enrollment sidecar must be skipped — it is not
    an autoskillit-owned trace (e.g. a test artifact or alien file)."""
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    trace = tmpfs / "autoskillit_trace_99997.jsonl"
    trace.write_text("")
    os.utime(trace, (time.time() - 60,) * 2)

    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path))

    assert count == 0
    assert trace.exists(), "Alien trace file must not be deleted"


def test_recover_crashed_sessions_skips_wrong_boot_id(tmp_path, monkeypatch):
    """An enrollment sidecar with a different boot_id must be rejected."""
    monkeypatch.setattr(
        "autoskillit.execution.session_log.read_boot_id",
        lambda: "current-boot-id",
    )
    tmpfs = tmp_path / "shm"
    tmpfs.mkdir()
    pid = 99996
    trace = tmpfs / f"autoskillit_trace_{pid}.jsonl"
    enrollment = tmpfs / f"autoskillit_enrollment_{pid}.json"
    trace.write_text("")
    enrollment.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": pid,
                "boot_id": "stale-boot-id",
                "starttime_ticks": 1234,
                "session_id": "",
                "enrolled_at": "2026-01-01T00:00:00+00:00",
                "kitchen_id": "",
                "order_id": "",
            }
        )
    )
    os.utime(trace, (time.time() - 60,) * 2)

    count = recover_crashed_sessions(tmpfs_path=str(tmpfs), log_dir=str(tmp_path))

    assert count == 0


def test_flush_writes_crash_exception_file(tmp_path):
    """When exception_text is provided, flush_session_log writes crash_exception.txt."""
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="test-session",
        pid=1234,
        skill_command="/test",
        success=False,
        subtype="crashed",
        exit_code=-1,
        start_ts=datetime.now(UTC).isoformat(),
        proc_snapshots=None,
        termination_reason="CRASHED",
        exception_text="RuntimeError: boom\n  at headless.py:1023",
    )
    session_dir = tmp_path / "sessions" / "test-session"
    crash_file = session_dir / "crash_exception.txt"
    assert crash_file.exists()
    assert "RuntimeError: boom" in crash_file.read_text()


# ---------------------------------------------------------------------------
# raw_stdout and per-turn field tests
# ---------------------------------------------------------------------------


def test_flush_session_log_writes_raw_stdout_on_failure(tmp_path):
    raw = '{"type": "assistant"}\n{"type": "result"}\n'
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="test-session",
        pid=1,
        skill_command="test",
        success=False,
        subtype="empty_output",
        exit_code=-1,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        raw_stdout=raw,
    )
    raw_file = tmp_path / "sessions" / "test-session" / "raw_stdout.jsonl"
    assert raw_file.exists()
    assert raw_file.read_text() == raw


def test_flush_session_log_no_raw_stdout_on_success(tmp_path):
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="ok-session",
        pid=1,
        skill_command="test",
        success=True,
        subtype="success",
        exit_code=0,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        raw_stdout='{"type": "result"}',
    )
    raw_file = tmp_path / "sessions" / "ok-session" / "raw_stdout.jsonl"
    assert not raw_file.exists()


def test_flush_session_log_summary_contains_per_turn_fields(tmp_path, monkeypatch):
    cb_log = tmp_path / "s.jsonl"
    cb_log.write_text(
        json.dumps(
            {"type": "assistant", "requestId": "req-001", "timestamp": "2026-04-15T07:00:00Z"}
        )
        + "\n"
        + json.dumps(
            {"type": "assistant", "requestId": "req-002", "timestamp": "2026-04-15T07:00:05Z"}
        )
        + "\n"
    )
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="s",
        pid=1,
        skill_command="test",
        success=False,
        subtype="empty_output",
        exit_code=-1,
        start_ts="2026-04-15T07:00:00Z",
        proc_snapshots=None,
        last_stop_reason="end_turn",
    )
    summary = json.loads((tmp_path / "sessions" / "s" / "summary.json").read_text())
    assert summary["last_stop_reason"] == "end_turn"
    assert summary["request_ids"] == ["req-001", "req-002"]
    assert summary["turn_timestamps"] == ["2026-04-15T07:00:00Z", "2026-04-15T07:00:05Z"]


# ---------------------------------------------------------------------------
# Silent gap, outcome anomaly, and exit snapshot tests
# ---------------------------------------------------------------------------


def test_summary_includes_silent_gap_seconds(tmp_path, monkeypatch):
    """silent_gap_seconds computed from cc_log mtime vs end_ts — approx 5.0s."""
    import autoskillit.execution.session_log as sl_mod

    cb_log = tmp_path / "session.jsonl"
    cb_log.write_text("")
    end_ts = "2026-04-15T07:00:10+00:00"
    end_dt = datetime.fromisoformat(end_ts)
    os.utime(cb_log, (end_dt.timestamp() - 5.0,) * 2)
    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    _flush(tmp_path, session_id="gap-test", end_ts=end_ts, proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "gap-test" / "summary.json").read_text())
    assert "silent_gap_seconds" in summary
    assert summary["silent_gap_seconds"] == pytest.approx(5.0, abs=0.5)


def test_summary_silent_gap_seconds_null_when_no_end_ts(tmp_path, monkeypatch):
    """silent_gap_seconds is null when end_ts is not provided."""
    import autoskillit.execution.session_log as sl_mod

    cb_log = tmp_path / "session.jsonl"
    cb_log.write_text("")
    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: cb_log)
    _flush(tmp_path, session_id="no-end-ts", end_ts="", proc_snapshots=None)
    summary = json.loads((tmp_path / "sessions" / "no-end-ts" / "summary.json").read_text())
    assert summary["silent_gap_seconds"] is None


def test_summary_silent_gap_seconds_null_when_cc_log_missing(tmp_path):
    """silent_gap_seconds is null when claude_code_log cannot be resolved."""
    _flush(
        tmp_path,
        session_id="no-cc-log",
        end_ts="2026-04-15T07:00:10+00:00",
        proc_snapshots=None,
        cwd="/nonexistent/path",
    )
    summary = json.loads((tmp_path / "sessions" / "no-cc-log" / "summary.json").read_text())
    assert summary["silent_gap_seconds"] is None


def test_flush_outcome_anomaly_included_in_anomaly_count(tmp_path, monkeypatch):
    """empty_result + output_tokens > 0 increments anomaly_count in summary and index."""
    import autoskillit.execution.session_log as sl_mod

    monkeypatch.setattr(sl_mod, "claude_code_log_path", lambda cwd, sid: None)
    _flush(
        tmp_path,
        session_id="outcome-anomaly",
        subtype="empty_result",
        success=False,
        token_usage={"output_tokens": 945, "input_tokens": 500},
        proc_snapshots=None,
    )
    summary = json.loads((tmp_path / "sessions" / "outcome-anomaly" / "summary.json").read_text())
    assert summary["anomaly_count"] >= 1
    anomalies_path = tmp_path / "sessions" / "outcome-anomaly" / "anomalies.jsonl"
    assert anomalies_path.exists()
    kinds = [json.loads(line)["kind"] for line in anomalies_path.read_text().splitlines() if line]
    assert "empty_result_with_tokens" in kinds
    index_entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert index_entry["anomaly_count"] >= 1


def test_proc_trace_preserves_exit_snapshot_event(tmp_path):
    """proc_trace.jsonl rows with event='exit_snapshot' preserve the marker."""
    exit_snap = {**_snap(), "event": "exit_snapshot"}
    _flush(
        tmp_path,
        session_id="exit-snap-test",
        proc_snapshots=[_snap(), _snap(), exit_snap],
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "sessions" / "exit-snap-test" / "proc_trace.jsonl")
        .read_text()
        .splitlines()
    ]
    assert rows[0]["event"] == "snapshot"
    assert rows[1]["event"] == "snapshot"
    assert rows[2]["event"] == "exit_snapshot"


# --- Versions block tests ---

_VERSIONS = {
    "autoskillit_version": "1.2.3",
    "install_type": "local-editable",
    "commit_id": None,
    "claude_code_version": "1.0.5",
    "plugins": [],
}


def test_summary_json_includes_versions_block(tmp_path):
    _flush(tmp_path, session_id="vs-001", versions=_VERSIONS)
    summary = json.loads((tmp_path / "sessions" / "vs-001" / "summary.json").read_text())
    assert "versions" in summary
    assert summary["versions"]["autoskillit_version"] == "1.2.3"
    assert summary["versions"]["claude_code_version"] == "1.0.5"


def test_summary_json_versions_includes_model_identifier(tmp_path):
    _flush(tmp_path, session_id="vs-002", versions=_VERSIONS, model_identifier="claude-opus-4")
    summary = json.loads((tmp_path / "sessions" / "vs-002" / "summary.json").read_text())
    assert summary["versions"]["model_identifier"] == "claude-opus-4"


def test_summary_json_omits_versions_when_not_passed(tmp_path):
    _flush(tmp_path, session_id="vs-003")
    summary = json.loads((tmp_path / "sessions" / "vs-003" / "summary.json").read_text())
    assert "versions" not in summary


def test_sessions_jsonl_includes_autoskillit_version(tmp_path):
    _flush(tmp_path, session_id="vs-004", versions=_VERSIONS)
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "vs-004")
    assert entry["autoskillit_version"] == "1.2.3"


def test_sessions_jsonl_includes_claude_code_version(tmp_path):
    _flush(tmp_path, session_id="vs-005", versions=_VERSIONS)
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "vs-005")
    assert entry["claude_code_version"] == "1.0.5"


def test_sessions_jsonl_autoskillit_version_empty_when_no_versions(tmp_path):
    _flush(tmp_path, session_id="vs-006")
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "vs-006")
    assert entry["autoskillit_version"] == ""


def test_session_log_includes_recipe_name(tmp_path):
    _flush(tmp_path, session_id="rp-001", recipe_name="impl")
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "rp-001")
    assert entry["recipe_name"] == "impl"


def test_session_log_includes_recipe_hashes(tmp_path):
    _flush(
        tmp_path,
        session_id="rp-002",
        recipe_content_hash="sha256:abc",
        recipe_composite_hash="sha256:def",
    )
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "rp-002")
    assert entry["recipe_content_hash"] == "sha256:abc"
    assert entry["recipe_composite_hash"] == "sha256:def"


def test_summary_includes_recipe_provenance(tmp_path):
    _flush(
        tmp_path,
        session_id="rp-003",
        recipe_name="impl",
        recipe_content_hash="sha256:abc",
        recipe_composite_hash="sha256:def",
        recipe_version="1.0.0",
    )
    session_dir = tmp_path / "sessions" / "rp-003"
    summary = json.loads((session_dir / "summary.json").read_text())
    assert "recipe_provenance" in summary
    assert summary["recipe_provenance"]["schema_version"] == 1
    assert summary["recipe_provenance"]["recipe_name"] == "impl"
    assert summary["recipe_provenance"]["content_hash"] == "sha256:abc"
    assert summary["recipe_provenance"]["composite_hash"] == "sha256:def"
    assert summary["recipe_provenance"]["recipe_version"] == "1.0.0"


def test_session_log_empty_recipe_identity(tmp_path):
    _flush(tmp_path, session_id="rp-004")
    entries = [
        json.loads(line)
        for line in (tmp_path / "sessions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    entry = next(e for e in entries if e["session_id"] == "rp-004")
    assert entry["recipe_name"] == ""
    assert entry["recipe_content_hash"] == ""
    assert entry["recipe_composite_hash"] == ""


def test_summary_no_recipe_provenance_when_empty(tmp_path):
    _flush(tmp_path, session_id="rp-005")
    session_dir = tmp_path / "sessions" / "rp-005"
    summary = json.loads((session_dir / "summary.json").read_text())
    assert "recipe_provenance" not in summary


def test_flush_index_includes_duration_seconds(tmp_path):
    """sessions.jsonl index entry includes duration_seconds."""
    _flush(tmp_path, elapsed_seconds=42.5)
    index = (tmp_path / "sessions.jsonl").read_text().strip()
    entry = json.loads(index)
    assert entry["duration_seconds"] == pytest.approx(42.5)


# --- Group H: campaign_id / dispatch_id schema tests ---


def test_flush_writes_campaign_id_to_summary(tmp_path):
    """summary.json contains campaign_id field when kwarg passed."""
    _flush(tmp_path, session_id="gh-001", campaign_id="camp-abc")
    summary = json.loads((tmp_path / "sessions" / "gh-001" / "summary.json").read_text())
    assert summary["campaign_id"] == "camp-abc"


def test_flush_writes_dispatch_id_to_summary(tmp_path):
    """summary.json contains dispatch_id field when kwarg passed."""
    _flush(tmp_path, session_id="gh-002", campaign_id="camp-abc", dispatch_id="disp-xyz")
    summary = json.loads((tmp_path / "sessions" / "gh-002" / "summary.json").read_text())
    assert summary["dispatch_id"] == "disp-xyz"


def test_flush_writes_campaign_id_to_index(tmp_path):
    """sessions.jsonl entry contains campaign_id."""
    _flush(tmp_path, session_id="gh-003", campaign_id="camp-abc")
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert entry["campaign_id"] == "camp-abc"


def test_flush_writes_dispatch_id_to_index(tmp_path):
    """sessions.jsonl entry contains dispatch_id."""
    _flush(tmp_path, session_id="gh-004", campaign_id="camp-abc", dispatch_id="disp-xyz")
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert entry["dispatch_id"] == "disp-xyz"


def test_flush_writes_meta_json_sidecar(tmp_path):
    """meta.json written with campaign_id and dispatch_id when campaign_id non-empty."""
    _flush(tmp_path, session_id="gh-005", campaign_id="c1", dispatch_id="d1")
    meta_path = tmp_path / "sessions" / "gh-005" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta == {"campaign_id": "c1", "dispatch_id": "d1"}


def test_flush_writes_meta_json_sidecar_campaign_only(tmp_path):
    """meta.json written with empty dispatch_id when only campaign_id is provided."""
    _flush(tmp_path, session_id="gh-005b", campaign_id="c1")
    meta_path = tmp_path / "sessions" / "gh-005b" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta == {"campaign_id": "c1", "dispatch_id": ""}


def test_flush_omits_meta_json_when_no_campaign(tmp_path):
    """No meta.json written when campaign_id is empty (default)."""
    _flush(tmp_path, session_id="gh-006")
    meta_path = tmp_path / "sessions" / "gh-006" / "meta.json"
    assert not meta_path.exists()


def test_flush_defaults_campaign_dispatch_empty(tmp_path):
    """Existing callers without new kwargs produce empty-string fields."""
    _flush(tmp_path, session_id="gh-007")
    summary = json.loads((tmp_path / "sessions" / "gh-007" / "summary.json").read_text())
    assert summary["campaign_id"] == ""
    assert summary["dispatch_id"] == ""
    entry = json.loads((tmp_path / "sessions.jsonl").read_text().strip())
    assert entry["campaign_id"] == ""
    assert entry["dispatch_id"] == ""


# --- Group M: retention protection tests ---


def _make_sessions(tmp_path, count, start_mtime=1_000_000_000, campaign_id=""):
    """Create `count` session directories with staggered mtimes.

    Returns list of dir_names in mtime order (oldest first).
    Seeds meta.json with campaign_id if provided.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    index_path = tmp_path / "sessions.jsonl"
    dir_names = []
    for i in range(count):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir(exist_ok=True)
        mtime = start_mtime + i
        os.utime(d, (mtime, mtime))
        if campaign_id:
            (d / "meta.json").write_text(
                json.dumps({"campaign_id": campaign_id, "dispatch_id": f"d-{i}"})
            )
        with index_path.open("a") as f:
            f.write(
                json.dumps(
                    {"session_id": dir_name, "dir_name": dir_name, "campaign_id": campaign_id}
                )
                + "\n"
            )
        dir_names.append(dir_name)
    return dir_names


def _make_state_file(project_dir, campaign_id, status):
    """Create a fleet dispatch state file."""
    dispatches_dir = project_dir / ".autoskillit" / "temp" / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    state_path = dispatches_dir / "d1.json"
    state_path.write_text(
        json.dumps(
            {
                "campaign_id": campaign_id,
                "dispatches": [{"name": "truck-1", "status": status}],
            }
        )
    )
    return state_path


def test_retention_protects_active_campaign_sessions(tmp_path, monkeypatch):
    """Sessions belonging to an active campaign survive retention even when expired."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Create 5 "non-campaign" sessions + 2 "active campaign" sessions at the oldest positions
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    # Oldest 2 dirs: campaign sessions (will be "expired" if not protected)
    for i in range(2):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        (d / "meta.json").write_text(
            json.dumps({"campaign_id": "active-campaign", "dispatch_id": f"d{i}"})
        )
        with index_path.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "session_id": dir_name,
                        "dir_name": dir_name,
                        "campaign_id": "active-campaign",
                    }
                )
                + "\n"
            )

    # Next 4 dirs: non-campaign sessions
    for i in range(2, 6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        with index_path.open("a") as f:
            f.write(
                json.dumps({"session_id": dir_name, "dir_name": dir_name, "campaign_id": ""})
                + "\n"
            )

    _make_state_file(project_dir, "active-campaign", "running")

    # Flush a 7th session to trigger retention (5 max, so 2 should expire)
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        get_protected_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Protected sessions survive — campaign meta.json writes update their mtime so they
    # end up in the "surviving" window; even if they landed in expired, protection saves them.
    assert (sessions_dir / "session-0000").exists(), "active campaign session must survive"
    assert (sessions_dir / "session-0001").exists(), "active campaign session must survive"
    # The 2 non-campaign sessions with oldest mtimes (0002, 0003) are deleted.
    # session-0002 and session-0003 retain the manually-set Sept-2001 mtimes (no file writes
    # update their directory mtime) so they are the oldest dirs overall.
    assert not (sessions_dir / "session-0002").exists(), (
        "oldest non-campaign session must be deleted"
    )
    assert not (sessions_dir / "session-0003").exists(), (
        "second oldest non-campaign session must be deleted"
    )
    # Newer non-campaign sessions survive (they are in the top-5 window)
    assert (sessions_dir / "session-0004").exists(), "session-0004 must survive"
    assert (sessions_dir / "session-0005").exists(), "session-0005 must survive"
    # Newly flushed session must be present
    assert (sessions_dir / "session-0006").exists()


def test_retention_deletes_released_campaign_sessions(tmp_path, monkeypatch):
    """Sessions whose campaign is in a terminal state are eligible for deletion."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    # Create 6 dirs — oldest 2 have meta.json but campaign is released
    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        if i < 2:
            (d / "meta.json").write_text(
                json.dumps({"campaign_id": "done-campaign", "dispatch_id": f"d{i}"})
            )
        with index_path.open("a") as f:
            cid = "done-campaign" if i < 2 else ""
            f.write(
                json.dumps({"session_id": dir_name, "dir_name": dir_name, "campaign_id": cid})
                + "\n"
            )
        # Set mtime AFTER all writes inside the dir to get the intended ordering
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))

    _make_state_file(project_dir, "done-campaign", "released")

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        get_protected_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Released campaign sessions are NOT protected — oldest 2 should be deleted
    assert not (sessions_dir / "session-0000").exists(), (
        "released campaign session must be deleted"
    )
    assert not (sessions_dir / "session-0001").exists(), (
        "released campaign session must be deleted"
    )


def test_retention_preserves_index_for_protected(tmp_path, monkeypatch):
    """Protected sessions' entries survive the sessions.jsonl rewrite."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        if i < 2:
            (d / "meta.json").write_text(
                json.dumps({"campaign_id": "live-campaign", "dispatch_id": f"d{i}"})
            )
        cid = "live-campaign" if i < 2 else ""
        with index_path.open("a") as f:
            f.write(
                json.dumps({"session_id": dir_name, "dir_name": dir_name, "campaign_id": cid})
                + "\n"
            )

    _make_state_file(project_dir, "live-campaign", "pending")

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        get_protected_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    index_lines = [ln for ln in index_path.read_text().strip().split("\n") if ln.strip()]
    dir_names_in_index = {json.loads(ln)["dir_name"] for ln in index_lines}
    assert "session-0000" in dir_names_in_index, "protected session index entry must be preserved"
    assert "session-0001" in dir_names_in_index, "protected session index entry must be preserved"


def test_retention_handles_missing_meta_json(tmp_path, monkeypatch):
    """Session dirs without meta.json are not protected (normal deletion)."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    _make_state_file(project_dir, "active-campaign", "running")

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        # No meta.json written — sessions are not linked to any campaign
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        get_protected_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Oldest dirs with no meta.json are deleted normally
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()


def test_retention_handles_missing_franchise_state_dir(tmp_path, monkeypatch):
    """No franchise state files → normal retention behavior (no crash)."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # No dispatches dir created — build_protected_campaign_ids returns empty frozenset
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        (d / "meta.json").write_text(
            json.dumps({"campaign_id": "some-campaign", "dispatch_id": f"d{i}"})
        )
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")
        # Set mtime AFTER all writes inside the dir to get the intended ordering
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))

    # Must not crash even though project_dir exists but has no dispatches dir
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        get_protected_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Normal retention applies — oldest dirs deleted
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()


def test_retention_handles_corrupt_meta_json(tmp_path, monkeypatch):
    """Malformed meta.json → session not protected (graceful degradation)."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_state_file(project_dir, "active-campaign", "running")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        if i < 2:
            # Write corrupt JSON so meta.json is unreadable
            (d / "meta.json").write_text("not valid json {{{{")
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")
        # Set mtime AFTER all writes inside the dir to get the intended ordering
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        get_protected_ids=build_protected_campaign_ids,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # Corrupt meta.json → not protected → deleted normally
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()


def test_session_log_removed_build_protected_function() -> None:
    """SL_CB_1: _build_protected_campaign_ids must not exist on session_log module."""
    import autoskillit.execution.session_log as sl_module

    assert not hasattr(sl_module, "_build_protected_campaign_ids")


def test_session_log_removed_terminal_statuses_constant() -> None:
    """SL_CB_2: _TERMINAL_DISPATCH_STATUSES must not exist on session_log module."""
    import autoskillit.execution.session_log as sl_module

    assert not hasattr(sl_module, "_TERMINAL_DISPATCH_STATUSES")


def test_retention_no_protection_when_callback_is_none(tmp_path: Path, monkeypatch) -> None:
    """SL_CB_6: get_protected_ids=None with active campaign → no protection applied."""
    import autoskillit.execution.session_log as sl_module

    monkeypatch.setattr(sl_module, "_MAX_SESSIONS", 5)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_state_file(project_dir, "active-campaign", "running")

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    index_path = tmp_path / "sessions.jsonl"

    for i in range(6):
        dir_name = f"session-{i:04d}"
        d = sessions_dir / dir_name
        d.mkdir()
        os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        if i < 2:
            (d / "meta.json").write_text(
                json.dumps({"campaign_id": "active-campaign", "dispatch_id": f"d{i}"})
            )
            # Reset mtime after meta.json write (writing a file bumps directory mtime)
            os.utime(d, (1_000_000_000 + i, 1_000_000_000 + i))
        with index_path.open("a") as f:
            f.write(json.dumps({"session_id": dir_name, "dir_name": dir_name}) + "\n")

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(project_dir),
        get_protected_ids=None,
        session_id="session-0006",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    # No protection applied — oldest sessions deleted even though campaign is active
    assert not (sessions_dir / "session-0000").exists()
    assert not (sessions_dir / "session-0001").exists()


def test_flush_session_log_passes_callback_to_enforce_retention(
    tmp_path: Path, monkeypatch
) -> None:
    """SL_CB_7: flush_session_log forwards get_protected_ids kwarg to _enforce_retention."""
    import autoskillit.execution.session_log as sl_module

    captured: list = []

    def fake_enforce_retention(log_root, project_dir="", get_protected_ids=None) -> None:
        captured.append(get_protected_ids)

    monkeypatch.setattr(sl_module, "_enforce_retention", fake_enforce_retention)

    sentinel = build_protected_campaign_ids
    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/some/project",
        project_dir=str(tmp_path),
        get_protected_ids=sentinel,
        session_id="session-cb7",
        pid=12345,
        skill_command="/autoskillit:implement",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-04-20T10:00:00+00:00",
        proc_snapshots=None,
    )

    assert (tmp_path / "sessions.jsonl").exists()
    assert len(captured) == 1
    assert captured[0] is sentinel
