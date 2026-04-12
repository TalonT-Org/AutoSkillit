"""Tests for Linux-only process tracing via psutil and /proc filesystem."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import anyio
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
    assert snap.fd_soft_limit > 0
    # hand-rolled fields
    assert isinstance(snap.sig_pnd, str) and len(snap.sig_pnd) > 0
    assert isinstance(snap.oom_score, int)
    assert isinstance(snap.wchan, str)
    # context switches (psutil-sourced)
    assert snap.ctx_switches_voluntary >= 0
    assert snap.ctx_switches_involuntary >= 0
    # cpu_percent field
    assert isinstance(snap.cpu_percent, float)
    assert snap.cpu_percent >= 0.0


@pytest.mark.anyio
async def test_tracing_handle_accumulates_snapshots(tmp_path):
    """LinuxTracingHandle accumulates snapshots during monitoring."""
    import subprocess

    import anyio

    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing

    proc = subprocess.Popen(["sleep", "2"])
    cfg = LinuxTracingConfig(enabled=True, proc_interval=0.1, tmpfs_path=str(tmp_path))

    async with anyio.create_task_group() as tg:
        handle = start_linux_tracing(pid=proc.pid, config=cfg, tg=tg)
        assert handle is not None
        await anyio.sleep(0.5)
        snapshots = handle.stop()
        tg.cancel_scope.cancel()

    assert len(snapshots) >= 1
    assert snapshots[0].state != ""
    proc.kill()
    proc.wait()


@pytest.mark.anyio
async def test_tracing_handle_stop_returns_snapshots(tmp_path):
    """stop() returns the accumulated snapshot list."""
    import os

    import anyio

    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing

    cfg = LinuxTracingConfig(enabled=True, proc_interval=0.1, tmpfs_path=str(tmp_path))

    async with anyio.create_task_group() as tg:
        handle = start_linux_tracing(pid=os.getpid(), config=cfg, tg=tg)
        assert handle is not None
        await anyio.sleep(0.3)
        result = handle.stop()
        tg.cancel_scope.cancel()

    assert isinstance(result, list)
    assert len(result) >= 1


@pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
def test_linux_tracing_available_on_linux():
    from autoskillit.execution.linux_tracing import LINUX_TRACING_AVAILABLE

    assert LINUX_TRACING_AVAILABLE is True


@pytest.mark.skipif(sys.platform == "linux", reason="Non-Linux only")
def test_linux_tracing_unavailable_on_non_linux():
    from autoskillit.execution.linux_tracing import LINUX_TRACING_AVAILABLE

    assert LINUX_TRACING_AVAILABLE is False


def test_noop_on_non_linux(monkeypatch, tmp_path):
    """start_linux_tracing is a no-op when LINUX_TRACING_AVAILABLE is False."""
    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution import linux_tracing

    monkeypatch.setattr(linux_tracing, "LINUX_TRACING_AVAILABLE", False)
    cfg = LinuxTracingConfig(enabled=True, proc_interval=1.0, tmpfs_path=str(tmp_path))
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


# --- captured_at tests ---


@pytest.mark.anyio
async def test_proc_monitor_stamps_unique_captured_at():
    """Each snapshot from proc_monitor has a distinct captured_at."""
    from autoskillit.execution.linux_tracing import proc_monitor

    snaps = []
    async for snap in proc_monitor(os.getpid(), 0.01):
        snaps.append(snap)
        if len(snaps) >= 2:
            break
    assert snaps[0].captured_at != snaps[1].captured_at


# --- streaming writer tests ---


@pytest.mark.anyio
async def test_start_linux_tracing_creates_trace_file(tmp_path):
    """When tmpfs_path is configured, start_linux_tracing opens a trace file."""
    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing

    config = LinuxTracingConfig(enabled=True, proc_interval=0.01, tmpfs_path=str(tmp_path))
    async with anyio.create_task_group() as tg:
        handle = start_linux_tracing(os.getpid(), config, tg)
        assert handle is not None
        await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    assert handle._trace_path is not None
    assert handle._trace_path.exists()
    handle.stop()


@pytest.mark.anyio
async def test_streaming_writes_each_snapshot_as_jsonl(tmp_path):
    """Each yielded snapshot appears as a JSONL line in the trace file."""
    import subprocess

    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing

    proc = subprocess.Popen(["sleep", "2"])
    config = LinuxTracingConfig(enabled=True, proc_interval=0.05, tmpfs_path=str(tmp_path))

    async with anyio.create_task_group() as tg:
        handle = start_linux_tracing(proc.pid, config, tg)
        assert handle is not None
        await anyio.sleep(0.2)
        tg.cancel_scope.cancel()

    # Save trace path and flush before stop() deletes the file on clean exit
    trace_path = handle._trace_path
    assert trace_path is not None
    if handle._trace_file is not None:
        handle._trace_file.flush()
    content = trace_path.read_text()
    snapshots = handle.stop()
    lines = content.strip().split("\n")
    assert len(lines) >= 1
    for line in lines:
        record = json.loads(line)
        assert "vm_rss_kb" in record
    assert len(lines) == len(snapshots)

    proc.kill()
    proc.wait()


def test_stop_closes_trace_file(tmp_path):
    """handle.stop() closes the file handle; _trace_file is None after."""

    from autoskillit.execution.linux_tracing import LinuxTracingHandle

    handle = LinuxTracingHandle()
    trace_path = tmp_path / "test_trace.jsonl"
    handle._trace_path = trace_path
    handle._trace_file = trace_path.open("w", buffering=1)

    handle.stop()
    assert handle._trace_file is None


def test_stop_idempotent(tmp_path):
    """Calling stop() twice does not raise."""
    from autoskillit.execution.linux_tracing import LinuxTracingHandle

    handle = LinuxTracingHandle()
    trace_path = tmp_path / "test_trace.jsonl"
    handle._trace_path = trace_path
    handle._trace_file = trace_path.open("w", buffering=1)

    handle.stop()
    handle.stop()  # second call must not raise


@pytest.mark.anyio
async def test_streaming_graceful_when_tmpfs_missing(tmp_path):
    """If tmpfs_path does not exist, tracing still works in-memory."""
    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing

    config = LinuxTracingConfig(
        enabled=True,
        proc_interval=0.01,
        tmpfs_path=str(tmp_path / "nonexistent"),
    )
    async with anyio.create_task_group() as tg:
        handle = start_linux_tracing(os.getpid(), config, tg)
        assert handle is not None
        await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    assert handle._trace_path is None
    assert handle._trace_file is None
    snapshots = handle.stop()
    assert isinstance(snapshots, list)
    assert len(snapshots) >= 1


def test_proc_snapshot_has_captured_at_field():
    """ProcSnapshot must have a captured_at field populated at creation time."""
    import os

    from autoskillit.execution.linux_tracing import read_proc_snapshot

    snap = read_proc_snapshot(os.getpid())
    assert snap is not None
    assert hasattr(snap, "captured_at")
    assert snap.captured_at  # non-empty
    # Must be UTC-aware ISO 8601
    dt = datetime.fromisoformat(snap.captured_at)
    assert dt.tzinfo is not None


@pytest.mark.anyio
async def test_proc_monitor_snapshots_have_distinct_timestamps(tmp_path):
    """Consecutive snapshots from proc_monitor must have distinct captured_at values."""
    import os

    import anyio

    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing

    config = LinuxTracingConfig(proc_interval=0.05, tmpfs_path=str(tmp_path))
    async with anyio.create_task_group() as tg:
        handle = start_linux_tracing(os.getpid(), config, tg)
        await anyio.sleep(0.2)
        result = handle.stop()
        tg.cancel_scope.cancel()
    assert len(result) >= 2
    timestamps = [s.captured_at for s in result]
    assert len(set(timestamps)) == len(timestamps), "All captured_at must be unique"


@pytest.mark.anyio
async def test_proc_monitor_persists_psutil_process_for_cpu_percent():
    """proc_monitor reports cpu_percent > 0 for a CPU-bound subprocess.

    This is only possible when a single psutil.Process is reused across iterations
    and its baseline is primed before the loop. A fresh Process per iteration always
    returns 0.0 on the first cpu_percent(interval=0) call.
    """
    import subprocess

    from autoskillit.execution.linux_tracing import proc_monitor

    proc = subprocess.Popen(
        [sys.executable, "-c", "while True: pass"],
    )
    try:
        snaps = []
        with anyio.fail_after(10):
            async for snap in proc_monitor(proc.pid, interval=0.1):
                snaps.append(snap)
                if len(snaps) >= 5:
                    break
        assert len(snaps) >= 3, "Need at least 3 snapshots"
        assert any(s.cpu_percent > 0.0 for s in snaps), (
            "At least one snapshot must show cpu_percent > 0.0 for a CPU-bound process"
        )
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.anyio
async def test_start_linux_tracing_writes_enrollment_sidecar(tmp_path):
    """start_linux_tracing must write autoskillit_enrollment_{pid}.json immediately."""
    import anyio

    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing

    cfg = LinuxTracingConfig(enabled=True, proc_interval=0.1, tmpfs_path=str(tmp_path))
    async with anyio.create_task_group() as tg:
        proc = await anyio.open_process(["sleep", "2"])
        handle = start_linux_tracing(proc.pid, cfg, tg)
        assert handle is not None
        enrollment = tmp_path / f"autoskillit_enrollment_{proc.pid}.json"
        assert enrollment.exists()
        data = json.loads(enrollment.read_text())
        assert data["schema_version"] == 1
        assert data["pid"] == proc.pid
        try:
            handle.stop()
        finally:
            proc.kill()
    assert not enrollment.exists(), "Enrollment must be deleted by stop()"


# --- LinuxTracingConfig guard tests ---


def test_tracing_config_rejects_dev_shm_in_test_env(monkeypatch):
    """LinuxTracingConfig.__post_init__ must raise when tmpfs_path is /dev/shm
    and PYTEST_CURRENT_TEST env var is set."""
    from autoskillit.config import LinuxTracingConfig

    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/execution/test_linux_tracing.py::fake_test",
    )
    with pytest.raises(RuntimeError, match="tmpfs_path|PYTEST_CURRENT_TEST"):
        LinuxTracingConfig(tmpfs_path="/dev/shm")


def test_tracing_config_allows_custom_tmpfs_in_test_env(monkeypatch, tmp_path):
    """LinuxTracingConfig must not raise when a non-/dev/shm path is provided."""
    from autoskillit.config import LinuxTracingConfig

    monkeypatch.setenv(
        "PYTEST_CURRENT_TEST",
        "tests/execution/test_linux_tracing.py::fake_test",
    )
    cfg = LinuxTracingConfig(tmpfs_path=str(tmp_path))  # must not raise
    assert cfg.tmpfs_path == str(tmp_path)


def test_tracing_config_allows_dev_shm_outside_test_env(monkeypatch):
    """LinuxTracingConfig must not raise for /dev/shm when not running under pytest."""
    from autoskillit.config import LinuxTracingConfig

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    cfg = LinuxTracingConfig(tmpfs_path="/dev/shm")  # production path — must not raise
    assert cfg.tmpfs_path == "/dev/shm"


def test_isolated_tracing_config_fixture_returns_non_dev_shm(isolated_tracing_config):
    """The isolated_tracing_config fixture must return a config pointing to a
    temp dir, not /dev/shm."""
    from pathlib import Path

    assert isolated_tracing_config.tmpfs_path != "/dev/shm"
    assert Path(isolated_tracing_config.tmpfs_path).is_dir()


@pytest.mark.anyio
async def test_stop_unlinks_trace_and_enrollment(tmp_path):
    """stop() must delete both trace JSONL and enrollment sidecar on clean exit."""
    import anyio

    from autoskillit.config import LinuxTracingConfig
    from autoskillit.execution.linux_tracing import start_linux_tracing

    cfg = LinuxTracingConfig(enabled=True, proc_interval=0.1, tmpfs_path=str(tmp_path))
    async with anyio.create_task_group() as tg:
        proc = await anyio.open_process(["sleep", "2"])
        handle = start_linux_tracing(proc.pid, cfg, tg)
        assert handle is not None
        trace = tmp_path / f"autoskillit_trace_{proc.pid}.jsonl"
        enrollment = tmp_path / f"autoskillit_enrollment_{proc.pid}.json"
        assert trace.exists()
        try:
            handle.stop()
        finally:
            proc.kill()
    assert not trace.exists(), "Trace file must be deleted on clean stop()"
    assert not enrollment.exists(), "Enrollment sidecar must be deleted on clean stop()"
