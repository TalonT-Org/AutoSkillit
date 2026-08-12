"""Behavioral tests for process cleanup evidence and owned-group authority."""

from __future__ import annotations

import signal
from typing import Any

import psutil
import pytest

from autoskillit.execution import async_kill_process_tree, kill_process_tree
from autoskillit.execution.process import _process_kill

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def test_missing_unowned_root_is_incomplete_evidence() -> None:
    result = kill_process_tree(999_999_999)

    assert result.root_pid == 999_999_999
    assert result.complete is False
    assert result.observation_complete is False
    assert result.process_identities == ()


@pytest.mark.asyncio
async def test_async_kill_returns_same_fail_closed_evidence() -> None:
    result = await async_kill_process_tree(999_999_999)

    assert result.observation_complete is False
    assert result.complete is False


def test_identity_denial_excludes_unverified_target_from_signals(
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
    monkeypatch.setattr(
        _process_kill.psutil,
        "wait_procs",
        lambda procs, *, timeout: (list(procs), []),
    )

    result = kill_process_tree(parent.pid)

    assert sent_signals == [(101, signal.SIGTERM)]
    assert result.process_identities == ((101, 123.0),)
    assert result.access_denied_pids == (202,)
    assert result.observation_complete is False
    assert result.complete is False


def test_wait_timeout_is_positive_survivor_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 101

        def children(self, *, recursive: bool) -> list[FakeProcess]:
            return []

        def create_time(self) -> float:
            return 123.0

        def send_signal(self, _sig: signal.Signals) -> None:
            return

    process = FakeProcess()
    monkeypatch.setattr(_process_kill.psutil, "Process", lambda _pid: process)
    monkeypatch.setattr(
        _process_kill.psutil,
        "wait_procs",
        lambda _procs, *, timeout: (_ for _ in ()).throw(
            psutil.TimeoutExpired(timeout, pid=process.pid)
        ),
    )

    result = kill_process_tree(process.pid, timeout=0)

    assert result.survivor_pids == (process.pid,)
    assert result.complete is False


def test_programming_errors_still_propagate() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        kill_process_tree(0)


class FakePopen:
    def __init__(self, _args: object, **_kwargs: object) -> None:
        self.pid = 321
        self.returncode: int | None = None

    def kill(self) -> None:
        self.returncode = -signal.SIGKILL

    def terminate(self) -> None:
        self.returncode = -signal.SIGTERM

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


def test_spawn_provenance_and_unreaped_leader_authorize_group_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(_process_kill.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(_process_kill.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(_process_kill.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    monkeypatch.setattr(
        _process_kill,
        "_snapshot_process_tree",
        lambda _pid: _process_kill.ProcessObservationSnapshot(),
    )

    owner = _process_kill.spawn_owned_process(["command"], start_new_session=True)
    owner._signal_group(signal.SIGTERM)

    assert signals == [(owner.pid, signal.SIGTERM)]


def test_missing_atomic_spawn_provenance_refuses_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        _process_kill.subprocess,
        "Popen",
        lambda *_args, **kwargs: popen_calls.append(kwargs),
    )

    with pytest.raises(ValueError, match="fresh-group mode"):
        _process_kill.spawn_owned_process(["command"])

    assert popen_calls == []


def test_reaped_leader_permanently_revokes_group_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, signal.Signals]] = []
    process = FakePopen([])
    monkeypatch.setattr(
        _process_kill,
        "_snapshot_process_tree",
        lambda _pid: _process_kill.ProcessObservationSnapshot(),
    )
    monkeypatch.setattr(_process_kill.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(_process_kill.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    owner = _process_kill.OwnedProcessGroup(process, process.pid)
    process.returncode = 0

    owner._signal_group(signal.SIGKILL)

    assert signals == []
    assert owner.snapshot.observation_complete is False
