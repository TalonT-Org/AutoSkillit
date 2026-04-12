"""Integration tests: full tracing pipeline (accumulation + flush) end-to-end."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta

import anyio
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux only")


@pytest.mark.anyio
async def test_full_tracing_pipeline_writes_distinct_timestamps(tmp_path):
    """End-to-end: snapshot accumulation + flush produces unique ts per record."""
    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing
    from autoskillit.execution.session_log import flush_session_log

    config = LinuxTracingConfig(proc_interval=0.05, tmpfs_path=str(tmp_path))
    start_ts = datetime.now(UTC).isoformat()
    start_mono = time.monotonic()
    async with anyio.create_task_group() as tg:
        handle = start_linux_tracing(os.getpid(), config, tg)
        await anyio.sleep(0.3)
        snaps = handle.stop()
        tg.cancel_scope.cancel()
    # Derive end_ts from monotonic elapsed to guard against WSL2 wall-clock regressions.
    # datetime.now(UTC) can go backward on WSL2 (NTP correction / host sleep-wake),
    # causing end_ts < start_ts and a spurious negative duration_seconds.
    elapsed = time.monotonic() - start_mono
    end_ts = (datetime.fromisoformat(start_ts) + timedelta(seconds=elapsed)).isoformat()
    assert len(snaps) >= 2, "Need at least 2 snapshots for timestamp variance test"
    snap_dicts = [s.__dict__ for s in snaps]

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id="integration-test-001",
        pid=os.getpid(),
        skill_command="/test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts=start_ts,
        end_ts=end_ts,
        elapsed_seconds=elapsed,
        termination_reason="natural_exit",
        snapshot_interval_seconds=0.05,
        proc_snapshots=snap_dicts,
    )

    session_dir = tmp_path / "sessions" / "integration-test-001"
    records = [
        json.loads(line) for line in (session_dir / "proc_trace.jsonl").read_text().splitlines()
    ]
    timestamps = [r["ts"] for r in records]
    assert len(set(timestamps)) == len(timestamps), "All ts must be unique per snapshot"
    assert all(t != start_ts for t in timestamps[1:]), "ts must not be the session start time"

    summary = json.loads((session_dir / "summary.json").read_text())
    assert "end_ts" in summary
    assert "duration_seconds" in summary
    assert summary["duration_seconds"] > 0
    assert summary["duration_seconds"] == pytest.approx(elapsed, abs=0.5), (
        "duration_seconds in summary.json must reflect monotonic elapsed, "
        "not wall-clock subtraction"
    )


# ---------------------------------------------------------------------------
# REQ-DIAG-001 — new anomaly kinds surface in anomalies.jsonl
# ---------------------------------------------------------------------------

_BASE_SNAP: dict[str, object] = {
    "captured_at": "2026-01-01T00:00:00+00:00",
    "vm_rss_kb": 100000,
    "threads": 4,
    "fd_count": 10,
    "fd_soft_limit": 1024,
    "ctx_switches_voluntary": 500,
    "ctx_switches_involuntary": 20,
    "sig_pnd": "0000000000000000",
    "sig_blk": "0000000000000000",
    "sig_cgt": "0000000000000000",
    "oom_score": 50,
    "cpu_percent": 0.0,
}


def _flush_with_snaps(tmp_path, session_id: str, snaps: list[dict]) -> None:
    from autoskillit.execution.session_log import flush_session_log

    flush_session_log(
        log_dir=str(tmp_path),
        cwd="/tmp",
        session_id=session_id,
        pid=12345,
        skill_command="/test",
        success=True,
        subtype="completed",
        exit_code=0,
        start_ts="2026-01-01T00:00:00+00:00",
        proc_snapshots=snaps,
    )


def test_flush_session_log_surfaces_d_state_sustained(tmp_path):
    """flush_session_log writes d_state_sustained to anomalies.jsonl (REQ-DIAG-001)."""
    snaps = [
        {**_BASE_SNAP, "state": "disk-sleep", "wchan": "ext4_file_write_iter"},
        {**_BASE_SNAP, "state": "disk-sleep", "wchan": "ext4_file_write_iter"},
    ]
    _flush_with_snaps(tmp_path, "diag-d-state-001", snaps)

    anomalies_path = tmp_path / "sessions" / "diag-d-state-001" / "anomalies.jsonl"
    assert anomalies_path.exists(), "anomalies.jsonl must be created when anomalies are detected"
    kinds = [json.loads(line)["kind"] for line in anomalies_path.read_text().splitlines()]
    assert "d_state_sustained" in kinds


def test_flush_session_log_surfaces_high_cpu_sustained(tmp_path):
    """flush_session_log writes high_cpu_sustained to anomalies.jsonl (REQ-DIAG-001)."""
    snaps = [
        {**_BASE_SNAP, "state": "sleeping", "wchan": "", "cpu_percent": 95.0},
        {**_BASE_SNAP, "state": "sleeping", "wchan": "", "cpu_percent": 95.0},
    ]
    _flush_with_snaps(tmp_path, "diag-high-cpu-001", snaps)

    anomalies_path = tmp_path / "sessions" / "diag-high-cpu-001" / "anomalies.jsonl"
    assert anomalies_path.exists(), "anomalies.jsonl must be created when anomalies are detected"
    kinds = [json.loads(line)["kind"] for line in anomalies_path.read_text().splitlines()]
    assert "high_cpu_sustained" in kinds
