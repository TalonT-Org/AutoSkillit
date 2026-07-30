"""Behavioral tests for process-tree cleanup evidence."""

from __future__ import annotations

import signal

import psutil
import pytest

from autoskillit.execution import async_kill_process_tree, kill_process_tree
from autoskillit.execution.process import _process_kill

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def test_kill_nonexistent_process_returns_complete_evidence() -> None:
    result = kill_process_tree(999_999_999)

    assert result.root_pid == 999_999_999
    assert result.complete is True
    assert result.process_identities == ()
    assert result.survivor_pids == ()


@pytest.mark.asyncio
async def test_async_kill_returns_same_typed_evidence() -> None:
    result = await async_kill_process_tree(999_999_999)

    assert result.root_pid == 999_999_999
    assert result.complete is True


def test_identity_access_denied_does_not_abort_tree_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_signals: list[tuple[int, signal.Signals]] = []

    class FakeProcess:
        def __init__(self, pid: int, *, deny_identity: bool = False) -> None:
            self.pid = pid
            self._deny_identity = deny_identity

        def children(self, *, recursive: bool) -> list[FakeProcess]:
            assert recursive is True
            return [FakeProcess(202, deny_identity=True)]

        def create_time(self) -> float:
            if self._deny_identity:
                raise psutil.AccessDenied(pid=self.pid)
            return 123.0

        def send_signal(self, sig: signal.Signals) -> None:
            sent_signals.append((self.pid, sig))

    parent = FakeProcess(101)
    monkeypatch.setattr(_process_kill.psutil, "Process", lambda _pid: parent)

    def fake_wait_procs(
        procs: list[FakeProcess],
        *,
        timeout: float,
    ) -> tuple[list[FakeProcess], list[FakeProcess]]:
        del timeout
        return list(procs), []

    monkeypatch.setattr(
        _process_kill.psutil,
        "wait_procs",
        fake_wait_procs,
    )

    result = kill_process_tree(parent.pid)

    assert sent_signals == [
        (202, signal.SIGTERM),
        (101, signal.SIGTERM),
    ]
    assert result.process_identities == ((101, 123.0),)
    assert result.access_denied_pids == (202,)
