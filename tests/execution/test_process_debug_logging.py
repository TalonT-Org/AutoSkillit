"""Tests for debug logging instrumentation in process.py."""

from __future__ import annotations

import textwrap

import pytest
import structlog.testing

from autoskillit.core.types import TerminationReason
from autoskillit.execution.process import RaceAccumulator, RaceSignals

pytestmark = [pytest.mark.layer("execution")]


@pytest.mark.anyio
async def test_run_managed_async_logs_entry(tmp_path):
    """run_managed_async emits debug log on entry with cmd, cwd, timeout."""
    from autoskillit.execution.process import run_managed_async

    cmd = ["python3", "-c", "print('hello')"]
    with structlog.testing.capture_logs() as logs:
        await run_managed_async(cmd=cmd, cwd=tmp_path, timeout=10)

    entry_logs = [r for r in logs if r.get("event") == "run_managed_async_entry"]
    assert entry_logs, (
        f"Expected run_managed_async_entry log, got events: {[r.get('event') for r in logs]}"
    )
    entry = entry_logs[0]
    assert entry["timeout"] == 10
    assert str(tmp_path) in entry["cwd"]


@pytest.mark.anyio
async def test_run_managed_async_logs_result(tmp_path):
    """run_managed_async emits debug log with result summary."""
    from autoskillit.execution.process import run_managed_async

    cmd = ["python3", "-c", "print('hello')"]
    with structlog.testing.capture_logs() as logs:
        await run_managed_async(cmd=cmd, cwd=tmp_path, timeout=10)

    result_logs = [r for r in logs if r.get("event") == "run_managed_async_result"]
    assert result_logs, (
        f"Expected run_managed_async_result log, got events: {[r.get('event') for r in logs]}"
    )
    r = result_logs[0]
    assert r["returncode"] == 0
    assert "stdout_len" in r


def test_resolve_termination_logs_signals():
    """resolve_termination logs full RaceSignals state."""
    from autoskillit.execution.process import resolve_termination

    signals = RaceSignals(
        process_exited=True,
        process_returncode=0,
        channel_a_confirmed=False,
        channel_b_status=None,
        channel_b_session_id="",
    )
    with structlog.testing.capture_logs() as logs:
        termination, channel = resolve_termination(signals)

    resolve_logs = [r for r in logs if r.get("event") == "resolve_termination"]
    assert resolve_logs, (
        f"Expected resolve_termination log, got events: {[r.get('event') for r in logs]}"
    )
    r = resolve_logs[0]
    assert r["process_exited"] is True
    assert r["process_returncode"] == 0
    assert "resolved_termination" in r
    assert "resolved_channel" in r


def test_race_signals_includes_channel_b_session_id():
    """RaceSignals carries the session ID discovered by Channel B."""
    signals = RaceSignals(
        process_exited=True,
        process_returncode=0,
        channel_a_confirmed=False,
        channel_b_status="completion",
        channel_b_session_id="abc-123",
    )
    assert signals.channel_b_session_id == "abc-123"


def test_race_accumulator_threads_session_id():
    """RaceAccumulator.to_race_signals() preserves channel_b_session_id."""
    acc = RaceAccumulator()
    acc.channel_b_status = "completion"
    acc.channel_b_session_id = "def-456"
    signals = acc.to_race_signals()
    assert signals.channel_b_session_id == "def-456"


@pytest.mark.anyio
async def test_watch_process_logs_exit(tmp_path):
    """_watch_process logs pid and returncode on exit."""
    from autoskillit.execution.process import run_managed_async

    # Use a process that exits quickly with code 0
    cmd = ["python3", "-c", "pass"]
    with structlog.testing.capture_logs() as logs:
        await run_managed_async(cmd=cmd, cwd=tmp_path, timeout=10)

    exit_logs = [r for r in logs if r.get("event") == "process_exited"]
    assert exit_logs, f"Expected process_exited log, got events: {[r.get('event') for r in logs]}"
    assert exit_logs[0]["returncode"] == 0


@pytest.mark.anyio
async def test_kill_decision_logs_natural_exit(tmp_path):
    """Kill decision logs 'no_kill' reason when process exits on its own."""
    from autoskillit.execution.process import run_managed_async

    cmd = ["python3", "-c", "pass"]
    with structlog.testing.capture_logs() as logs:
        await run_managed_async(cmd=cmd, cwd=tmp_path, timeout=10)

    kill_logs = [r for r in logs if r.get("event") == "kill_decision"]
    assert kill_logs, f"Expected kill_decision log, got events: {[r.get('event') for r in logs]}"
    assert kill_logs[0]["reason"] == "no_kill"


@pytest.mark.anyio
async def test_kill_decision_logs_timeout(tmp_path):
    """Kill decision logs 'immediate_kill' reason when process exceeds timeout."""
    from autoskillit.execution.process import run_managed_async

    cmd = [
        "python3",
        "-c",
        textwrap.dedent("""\
        import time
        time.sleep(30)
    """),
    ]
    with structlog.testing.capture_logs() as logs:
        result = await run_managed_async(cmd=cmd, cwd=tmp_path, timeout=1)

    assert result.termination == TerminationReason.TIMED_OUT
    kill_logs = [r for r in logs if r.get("event") == "kill_decision"]
    assert kill_logs, f"Expected kill_decision log, got events: {[r.get('event') for r in logs]}"
    assert kill_logs[0]["reason"] == "immediate_kill"
