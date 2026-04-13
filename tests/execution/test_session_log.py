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
