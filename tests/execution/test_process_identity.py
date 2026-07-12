"""Tests for starttime_ticks=0 identity degradation warning in run_managed_async."""

from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from signal import SIGTERM
from unittest.mock import patch

import psutil
import pytest
import structlog.testing

from autoskillit.core.types import ProcessIdentity
from autoskillit.execution.process import run_managed_async
from autoskillit.execution.process._process_ownership import (
    OwnedProcessIdentityTracker,
    _IdentityStatus,
    inspect_pid_identity,
    is_pid_alive,
    signal_process_identity,
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


def test_seed_root_is_no_io_and_marks_identity_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected(*_args, **_kwargs):
        raise AssertionError("identity I/O during seed")

    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.psutil.Process", _unexpected
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.read_starttime_ticks", _unexpected
    )
    monkeypatch.setattr("autoskillit.execution.process._process_ownership.os.getpgid", _unexpected)
    monkeypatch.setattr("autoskillit.execution.process._process_ownership.os.getsid", _unexpected)
    tracker = OwnedProcessIdentityTracker()

    tracker.seed_root(123, process_group_id=123, session_id=123)

    assert tracker.root_pid == 123
    assert tracker.process_group_id == 123
    assert tracker.session_id == 123
    assert not tracker.root_identity_known
    assert tracker.snapshot_unknown_identities()[0].root_pid == 123


@pytest.mark.parametrize("error_type", [psutil.NoSuchProcess, psutil.AccessDenied])
def test_root_enrichment_fails_closed_and_retains_unknown_identity(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    tracker = OwnedProcessIdentityTracker()
    tracker.seed_root(123, process_group_id=123, session_id=123)
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.read_starttime_ticks",
        lambda _pid: 1000,
    )

    def _raise(_pid: int):
        raise error_type(123)

    monkeypatch.setattr("autoskillit.execution.process._process_ownership.psutil.Process", _raise)

    assert not tracker.enrich_root_identity()
    assert not tracker.root_identity_known
    assert tracker.snapshot_unknown_identities() == (
        ProcessIdentity(
            root_pid=123,
            starttime_ticks=0,
            fallback_create_time=0.0,
            process_group_id=123,
            session_id=123,
        ),
    )


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


def test_identity_mismatch_fails_closed_without_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sends: list[int] = []

    class _Process:
        def send_signal(self, signal_number: int) -> None:
            sends.append(signal_number)

    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.psutil.pid_exists",
        lambda _pid: True,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.read_starttime_ticks",
        lambda _pid: 2000,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.psutil.Process",
        lambda _pid: _Process(),
    )
    identity = ProcessIdentity(root_pid=123, starttime_ticks=1000)

    assert inspect_pid_identity(identity) is _IdentityStatus.UNKNOWN
    assert signal_process_identity(identity, SIGTERM) is _IdentityStatus.UNKNOWN
    assert sends == []


def test_signal_validates_and_signals_same_psutil_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handles: list[_Process] = []

    class _Process:
        def __init__(self) -> None:
            self.signals: list[int] = []

        def send_signal(self, signal_number: int) -> None:
            self.signals.append(signal_number)

    def _open_process(_pid: int) -> _Process:
        process = _Process()
        handles.append(process)
        return process

    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.psutil.Process", _open_process
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_ownership.read_starttime_ticks",
        lambda _pid: 1000,
    )
    identity = ProcessIdentity(root_pid=123, starttime_ticks=1000)

    assert signal_process_identity(identity, SIGTERM) is _IdentityStatus.ALIVE
    assert len(handles) == 1
    assert handles[0].signals == [SIGTERM]


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
