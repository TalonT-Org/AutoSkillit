"""Tests for Linux-only process tracing via psutil and /proc filesystem."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Linux-only tracing tests",
)

PROC_STATUS_FIXTURE = """\
Name:\tclaude
State:\tS (sleeping)
Tgid:\t12345
Pid:\t12345
PPid:\t12300
VmRSS:\t 245680 kB
Threads:\t4
SigPnd:\t0000000000000000
SigBlk:\t0000000000010000
SigCgt:\t0000000180014a07
voluntary_ctxt_switches:\t500
nonvoluntary_ctxt_switches:\t20
"""

PROC_OOM_SCORE_FIXTURE = "133"


def test_parse_proc_status_signal_masks():
    """Parse /proc/pid/status fixture for signal mask fields."""
    from autoskillit.execution.linux_tracing import _parse_proc_status

    fields = _parse_proc_status(PROC_STATUS_FIXTURE)
    assert fields["sig_pnd"] == "0000000000000000"
    assert fields["sig_blk"] == "0000000000010000"
    assert fields["sig_cgt"] == "0000000180014a07"


def test_read_proc_snapshot_missing_pid():
    """read_proc_snapshot returns None for nonexistent PID."""
    from autoskillit.execution.linux_tracing import read_proc_snapshot

    result = read_proc_snapshot(999999999)
    assert result is None


def test_read_proc_snapshot_has_all_fields():
    """read_proc_snapshot of current process returns all expected fields."""
    import os

    from autoskillit.execution.linux_tracing import read_proc_snapshot

    snap = read_proc_snapshot(os.getpid())
    assert snap is not None
    # psutil-sourced fields
    assert snap.state != ""
    assert snap.vm_rss_kb > 0
    assert snap.threads >= 1
    assert snap.fd_count > 0
    # hand-rolled fields
    assert isinstance(snap.sig_pnd, str) and len(snap.sig_pnd) > 0
    assert isinstance(snap.oom_score, int)
    assert isinstance(snap.wchan, str)
    # context switches (psutil-sourced)
    assert snap.ctx_switches_voluntary >= 0
    assert snap.ctx_switches_involuntary >= 0


def test_log_snapshot_delta_only_changed():
    """log_snapshot_delta logs only fields that changed between snapshots."""
    import structlog.testing

    from autoskillit.execution.linux_tracing import ProcSnapshot, log_snapshot_delta

    prev = ProcSnapshot(
        state="S (sleeping)",
        vm_rss_kb=100000,
        oom_score=50,
        fd_count=10,
        sig_pnd="0" * 16,
        sig_blk="0" * 16,
        sig_cgt="0" * 16,
        threads=4,
        wchan="",
        ctx_switches_voluntary=500,
        ctx_switches_involuntary=20,
    )
    curr = ProcSnapshot(
        state="S (sleeping)",
        vm_rss_kb=150000,
        oom_score=80,
        fd_count=10,
        sig_pnd="0" * 16,
        sig_blk="0" * 16,
        sig_cgt="0" * 16,
        threads=4,
        wchan="",
        ctx_switches_voluntary=510,
        ctx_switches_involuntary=35,
    )

    with structlog.testing.capture_logs() as logs:
        log_snapshot_delta(prev, curr, pid=12345)

    delta_logs = [r for r in logs if r.get("event") == "proc_snapshot_delta"]
    assert delta_logs, (
        f"Expected proc_snapshot_delta log, got events: {[r.get('event') for r in logs]}"
    )
    changes = delta_logs[0]["changes"]
    # Should include vm_rss change and oom_score change, but not state or fd_count
    assert "vm_rss_kb" in changes
    assert "oom_score" in changes
    assert "ctx_switches_involuntary" in changes
    assert "state" not in changes
    assert "fd_count" not in changes


def test_linux_tracing_available_flag():
    """LINUX_TRACING_AVAILABLE is True on Linux, False elsewhere."""
    from autoskillit.execution.linux_tracing import LINUX_TRACING_AVAILABLE

    if sys.platform == "linux":
        assert LINUX_TRACING_AVAILABLE is True
    else:
        assert LINUX_TRACING_AVAILABLE is False


def test_noop_on_non_linux(monkeypatch):
    """start_linux_tracing is a no-op when LINUX_TRACING_AVAILABLE is False."""
    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution import linux_tracing

    monkeypatch.setattr(linux_tracing, "LINUX_TRACING_AVAILABLE", False)
    cfg = LinuxTracingConfig(enabled=True, proc_interval=1.0)
    result = linux_tracing.start_linux_tracing(pid=1, config=cfg, tg=None)
    assert result is None


@pytest.mark.anyio
async def test_proc_monitor_detects_death():
    """proc_monitor stops when the target PID no longer exists."""
    import subprocess

    from autoskillit.execution.linux_tracing import proc_monitor

    proc = subprocess.Popen(["sleep", "0.5"])
    snapshots = []

    async for snap in proc_monitor(proc.pid, interval=0.1):
        snapshots.append(snap)

    assert len(snapshots) >= 1
