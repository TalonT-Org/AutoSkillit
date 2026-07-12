"""Tests for starttime_ticks=0 identity degradation warning in run_managed_async."""

from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog.testing

from autoskillit.execution.process import run_managed_async
from autoskillit.execution.process._process_ownership import (
    OwnedProcessIdentityTracker,
    is_pid_alive,
    time_remaining,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


class _ProcessInfo:
    def __init__(self, pid: int, create_time: float) -> None:
        self.info = {"pid": pid, "create_time": create_time}


def test_tracker_refresh_retains_matching_process_group_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = OwnedProcessIdentityTracker(
        root_pid=100,
        root_starttime_ticks=1000,
        root_fallback_create_time=1.0,
        process_group_id=10,
        session_id=20,
        captured={100: (1000, 1.0)},
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.psutil.process_iter",
        lambda *, attrs: [_ProcessInfo(101, 2.0), _ProcessInfo(102, 3.0)],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.os.getpgid",
        lambda pid: 10 if pid == 101 else 99,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.os.getsid",
        lambda pid: 99,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.read_starttime_ticks",
        lambda pid: {101: 2000, 102: 3000}[pid],
    )

    assert tracker.refresh_from_process_group() == 1
    assert tracker.captured == {100: (1000, 1.0), 101: (2000, 2.0)}


def test_is_pid_alive_uses_ticks_despite_create_time_jitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Process:
        def create_time(self) -> float:
            return 200.0

        def oneshot(self):
            return nullcontext()

    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.psutil.Process",
        lambda _pid: _Process(),
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.psutil.pid_exists",
        lambda _pid: True,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.read_starttime_ticks",
        lambda _pid: 2000,
    )

    assert is_pid_alive(123, 2000, 100.0)
    assert not is_pid_alive(123, 1000, 100.0)


def test_is_pid_alive_uses_exact_create_time_only_without_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Process:
        def create_time(self) -> float:
            return 200.0

        def oneshot(self):
            return nullcontext()

    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.psutil.Process",
        lambda _pid: _Process(),
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.psutil.pid_exists",
        lambda _pid: True,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.read_starttime_ticks",
        lambda _pid: None,
    )

    assert is_pid_alive(123, 0, 200.0)
    assert not is_pid_alive(123, 0, 199.0)


def test_time_remaining_is_bounded_at_zero() -> None:
    assert time_remaining(10.0, now=4.0) == 6.0
    assert time_remaining(10.0, now=11.0) == 0.0


class TestStarttimeTicksZeroWarning:
    """on_pid_resolved emits a warning when starttime_ticks cannot be resolved."""

    @pytest.mark.anyio
    async def test_on_pid_resolved_warns_when_ticks_zero(self, tmp_path: Path) -> None:
        """run_managed_async logs starttime_ticks_zero warning when ticks fall back to 0."""
        shim = tmp_path / "quick_exit.py"
        shim.write_text("import sys; sys.exit(0)")

        received: list[tuple[int, int]] = []

        def on_pid_resolved(pid: int, ticks: int) -> None:
            received.append((pid, ticks))

        with (
            structlog.testing.capture_logs() as logs,
            patch("autoskillit.execution.process.read_starttime_ticks", return_value=0),
        ):
            await run_managed_async(
                [sys.executable, str(shim)],
                cwd=tmp_path,
                timeout=10.0,
                on_pid_resolved=on_pid_resolved,
            )

        assert received, "on_pid_resolved callback was never called"
        assert all(ticks == 0 for _, ticks in received), (
            f"Expected ticks=0 for all callbacks, got: {received}"
        )
        warning_events = [
            log["event"] for log in logs if log.get("event") == "starttime_ticks_zero"
        ]
        assert warning_events, f"Expected 'starttime_ticks_zero' warning in logs, captured: {logs}"
