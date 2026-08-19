"""Behavioral tests for process cleanup evidence and owned-group authority."""

from __future__ import annotations

import errno
import signal
from collections.abc import Generator
from pathlib import Path
from typing import Any

import psutil
import pytest
import structlog.testing

from autoskillit.execution import TetherSpec, async_kill_process_tree, kill_process_tree
from autoskillit.execution.process import _process_kill

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


@pytest.fixture(scope="module", autouse=True)
def _fake_spawn_identity() -> Generator[None, None, None]:
    """Fake Linux process-identity reads for the module.

    ``spawn_owned_process``'s fail-closed identity guard reads the real
    ``/proc`` for the spawned child's start-time ticks and the host boot ID;
    ``FakePopen`` below hands back a synthetic, nonexistent pid, so the real
    readers would return ``None`` and the guard would raise. Pin both readers
    to fixed fake values for every test in this module.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_process_kill, "read_boot_id", lambda: "test-boot-id")
        mp.setattr(_process_kill, "read_starttime_ticks", lambda _pid: 12345)
        yield


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


@pytest.mark.parametrize(
    "disappearance",
    [
        psutil.NoSuchProcess(pid=101),
        ProcessLookupError(errno.ESRCH, "gone"),
    ],
)
def test_signal_disappearance_is_expected_complete_evidence(
    monkeypatch: pytest.MonkeyPatch,
    disappearance: BaseException,
) -> None:
    class FakeProcess:
        pid = 101

        def children(self, *, recursive: bool) -> list[FakeProcess]:
            assert recursive is True
            return []

        def create_time(self) -> float:
            return 123.0

        def send_signal(self, _sig: signal.Signals) -> None:
            raise disappearance

    process = FakeProcess()
    monkeypatch.setattr(_process_kill.psutil, "Process", lambda _pid: process)
    monkeypatch.setattr(
        _process_kill.psutil,
        "wait_procs",
        lambda _procs, *, timeout: ([], []),
    )

    result = kill_process_tree(process.pid)

    assert result.terminated_pids == (process.pid,)
    assert result.access_denied_pids == ()
    assert result.observation_complete is True
    assert result.complete is True


@pytest.mark.parametrize(
    "disappearance",
    [
        psutil.NoSuchProcess(pid=101),
        ProcessLookupError(errno.ESRCH, "gone"),
    ],
)
def test_wait_disappearance_is_expected_complete_evidence(
    monkeypatch: pytest.MonkeyPatch,
    disappearance: BaseException,
) -> None:
    class FakeProcess:
        pid = 101

        def children(self, *, recursive: bool) -> list[FakeProcess]:
            assert recursive is True
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
        lambda _procs, *, timeout: (_ for _ in ()).throw(disappearance),
    )

    result = kill_process_tree(process.pid)

    assert result.terminated_pids == (process.pid,)
    assert result.access_denied_pids == ()
    assert result.observation_complete is True
    assert result.complete is True


@pytest.mark.parametrize(
    ("enumeration_error", "expected_denied"),
    [
        (psutil.AccessDenied(pid=101), (101,)),
        (OSError(errno.EIO, "process table unavailable"), ()),
    ],
)
def test_partial_descendant_enumeration_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    enumeration_error: BaseException,
    expected_denied: tuple[int, ...],
) -> None:
    class FakeProcess:
        pid = 101

        def children(self, *, recursive: bool) -> list[FakeProcess]:
            assert recursive is True
            raise enumeration_error

        def create_time(self) -> float:
            return 123.0

        def send_signal(self, _sig: signal.Signals) -> None:
            return

    process = FakeProcess()
    monkeypatch.setattr(_process_kill.psutil, "Process", lambda _pid: process)
    monkeypatch.setattr(
        _process_kill.psutil,
        "wait_procs",
        lambda procs, *, timeout: (list(procs), []),
    )

    result = kill_process_tree(process.pid)

    assert result.process_identities == ((process.pid, 123.0),)
    assert result.terminated_pids == (process.pid,)
    assert result.access_denied_pids == expected_denied
    assert result.observation_complete is False
    assert result.complete is False


@pytest.mark.parametrize(
    "lookup_error",
    [psutil.Error("process table failed"), OSError(errno.EIO, "process table failed")],
)
def test_operational_root_lookup_errors_return_incomplete_evidence(
    monkeypatch: pytest.MonkeyPatch,
    lookup_error: BaseException,
) -> None:
    def fail_lookup(_pid: int) -> None:
        raise lookup_error

    monkeypatch.setattr(_process_kill.psutil, "Process", fail_lookup)

    result = kill_process_tree(101)

    assert result.process_identities == ()
    assert result.access_denied_pids == ()
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


def test_wait_permission_denial_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 101

        def children(self, *, recursive: bool) -> list[FakeProcess]:
            assert recursive is True
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
        lambda _procs, *, timeout: (_ for _ in ()).throw(psutil.AccessDenied(pid=process.pid)),
    )

    result = kill_process_tree(process.pid, timeout=0)

    assert result.access_denied_pids == (process.pid,)
    assert result.observation_complete is False
    assert result.complete is False


def test_os_signal_permission_denial_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 101

        def children(self, *, recursive: bool) -> list[FakeProcess]:
            assert recursive is True
            return []

        def create_time(self) -> float:
            return 123.0

        def send_signal(self, _sig: signal.Signals) -> None:
            raise PermissionError(errno.EPERM, "denied")

    process = FakeProcess()
    monkeypatch.setattr(_process_kill.psutil, "Process", lambda _pid: process)
    monkeypatch.setattr(
        _process_kill.psutil,
        "wait_procs",
        lambda procs, *, timeout: ([], list(procs)),
    )

    result = kill_process_tree(process.pid, timeout=0)

    assert result.access_denied_pids == (process.pid,)
    assert result.observation_complete is False
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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

    owner = _process_kill.spawn_owned_process(
        ["command"],
        start_new_session=True,
        tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
    )
    owner._signal_group(signal.SIGTERM)

    assert signals == [(owner.pid, signal.SIGTERM)]


def test_spawn_preserves_identity_exception_when_reap_times_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ReapTimeoutPopen(FakePopen):
        def wait(self, timeout: float | None = None) -> int:
            raise _process_kill.subprocess.TimeoutExpired("command", timeout)

    identity_error = KeyboardInterrupt()
    monkeypatch.setattr(_process_kill.subprocess, "Popen", ReapTimeoutPopen)
    monkeypatch.setattr(
        _process_kill.os,
        "getpgid",
        lambda _pid: (_ for _ in ()).throw(identity_error),
    )

    with pytest.raises(KeyboardInterrupt) as raised:
        _process_kill.spawn_owned_process(
            ["command"],
            start_new_session=True,
            tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
        )

    assert raised.value is identity_error


def test_spawn_validation_error_is_not_masked_by_reap_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ReapTimeoutPopen(FakePopen):
        def wait(self, timeout: float | None = None) -> int:
            raise _process_kill.subprocess.TimeoutExpired("command", timeout)

    monkeypatch.setattr(_process_kill.subprocess, "Popen", ReapTimeoutPopen)
    monkeypatch.setattr(_process_kill.os, "getpgid", lambda pid: pid + 1)

    with pytest.raises(RuntimeError, match="did not establish owned group leadership"):
        _process_kill.spawn_owned_process(
            ["command"],
            start_new_session=True,
            tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
        )


def test_missing_atomic_spawn_provenance_refuses_ownership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    popen_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        _process_kill.subprocess,
        "Popen",
        lambda *_args, **kwargs: popen_calls.append(kwargs),
    )

    with pytest.raises(ValueError, match="fresh-group mode"):
        _process_kill.spawn_owned_process(
            ["command"],
            tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
        )

    assert popen_calls == []


def test_reaped_leader_permanently_revokes_group_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(_process_kill.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        _process_kill,
        "_snapshot_process_tree",
        lambda _pid: _process_kill.ProcessObservationSnapshot(),
    )
    monkeypatch.setattr(_process_kill.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(_process_kill.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    owner = _process_kill.spawn_owned_process(
        ["command"],
        start_new_session=True,
        tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
    )
    owner.process.returncode = 0

    owner._signal_group(signal.SIGKILL)

    assert signals == []
    assert owner.snapshot.observation_complete is False


def test_sigkill_escalation_uses_final_direct_reap_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wait_timeouts: list[float | None] = []

    class RecordingPopen(FakePopen):
        def wait(self, timeout: float | None = None) -> int:
            wait_timeouts.append(timeout)
            return super().wait(timeout)

    monkeypatch.setattr(_process_kill.subprocess, "Popen", RecordingPopen)
    monkeypatch.setattr(_process_kill.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        _process_kill,
        "_snapshot_process_tree",
        lambda _pid: _process_kill.ProcessObservationSnapshot(),
    )
    owner = _process_kill.spawn_owned_process(
        ["command"],
        start_new_session=True,
        tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
    )
    monkeypatch.setattr(owner, "capture_snapshot", lambda: owner.snapshot)
    monkeypatch.setattr(owner, "_scan_group", lambda: ())
    monkeypatch.setattr(owner, "_signal_group", lambda _signum: None)
    monkeypatch.setattr(owner, "_wait_group_members", lambda _timeout: ())
    monkeypatch.setattr(owner, "observe_exit", lambda: None)

    owner.cleanup(timeout=7.0)

    assert wait_timeouts == [_process_kill._FINAL_WAIT_SECONDS]


def test_settle_preserving_converts_cleanup_failure_to_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_process_kill.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(_process_kill.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        _process_kill,
        "_snapshot_process_tree",
        lambda _pid: _process_kill.ProcessObservationSnapshot(),
    )
    owner = _process_kill.spawn_owned_process(
        ["command"],
        start_new_session=True,
        tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
    )
    cleanup_error = OSError(errno.EIO, "cleanup failed")
    monkeypatch.setattr(
        owner,
        "cleanup",
        lambda _timeout: (_ for _ in ()).throw(cleanup_error),
    )
    original_error = RuntimeError("original failure")

    result = owner.settle_preserving(original_error)

    assert result.complete is False
    assert result.survivor_pids == (owner.pid,)
    assert any("OSError" in note and "cleanup failed" in note for note in original_error.__notes__)


def test_unexpected_group_authority_error_is_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_process_kill.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(_process_kill.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        _process_kill,
        "_snapshot_process_tree",
        lambda _pid: _process_kill.ProcessObservationSnapshot(),
    )
    owner = _process_kill.spawn_owned_process(
        ["command"],
        start_new_session=True,
        tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
    )
    monkeypatch.setattr(
        _process_kill.os,
        "getpgid",
        lambda _pid: (_ for _ in ()).throw(OSError(errno.EIO, "identity unavailable")),
    )

    with structlog.testing.capture_logs() as logs:
        owner._signal_group(signal.SIGTERM)

    assert any(entry.get("event") == "owned_group_authority_validation_failed" for entry in logs)
    assert owner.snapshot.observation_complete is False


def test_arbitrary_handle_cannot_be_adopted_as_owned_group() -> None:
    process = FakePopen([])

    with pytest.raises(TypeError, match="spawn_owned_process"):
        _process_kill.OwnedProcessGroup(process, process.pid)


def _spawn_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> _process_kill.OwnedProcessGroup:
    monkeypatch.setattr(_process_kill.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(_process_kill.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        _process_kill,
        "_snapshot_process_tree",
        lambda _pid: _process_kill.ProcessObservationSnapshot(),
    )
    return _process_kill.spawn_owned_process(
        ["command"],
        start_new_session=True,
        tether=TetherSpec(origin="test", ceiling_seconds=60.0, tether_dir=tmp_path),
    )


def test_identity_is_alive_returns_false_for_zombie(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ZombieProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def create_time(self) -> float:
            return 123.0

        def status(self) -> str:
            return psutil.STATUS_ZOMBIE

    owner = _spawn_owner(monkeypatch, tmp_path)
    monkeypatch.setattr(_process_kill.psutil, "Process", lambda pid: ZombieProcess(pid))

    assert owner._identity_is_alive((101, 123.0)) is False


def test_cleanup_completes_when_only_survivor_is_zombie(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A zombie descendant must not land in survivor_pids — cleanup() reports complete."""

    class ZombieProcess:
        def __init__(self, pid: int, create_time: float) -> None:
            self.pid = pid
            self._create_time = create_time

        def create_time(self) -> float:
            return self._create_time

        def status(self) -> str:
            return psutil.STATUS_ZOMBIE

    zombie_identity = (99999, 111.0)
    owner = _spawn_owner(monkeypatch, tmp_path)
    owner.merge_snapshot(
        _process_kill.ProcessObservationSnapshot(
            process_identities=(zombie_identity,), observation_complete=True
        )
    )
    monkeypatch.setattr(owner, "_scan_group", lambda: (zombie_identity,))
    monkeypatch.setattr(owner, "_signal_group", lambda _signum: None)
    monkeypatch.setattr(
        _process_kill.psutil,
        "Process",
        lambda pid: ZombieProcess(pid, zombie_identity[1]),
    )
    owner.process.returncode = 0

    returncode, result = owner.cleanup(timeout=0.05)

    assert returncode == 0
    assert result.survivor_pids == ()
    assert result.complete is True


def test_settle_still_raises_on_genuinely_incomplete_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """settle() must keep raising for non-zombie incompleteness — cli/session depends on it."""
    owner = _spawn_owner(monkeypatch, tmp_path)
    incomplete_result = _process_kill.ProcessCleanupResult(
        root_pid=owner.pid,
        access_denied_pids=(1234,),
        observation_complete=True,
    )
    monkeypatch.setattr(owner, "cleanup", lambda timeout: (0, incomplete_result))

    with pytest.raises(_process_kill.OwnedProcessCleanupError):
        owner.settle()


@pytest.mark.parametrize(
    ("returncode", "complete", "expect_incomplete_log"),
    [
        (0, False, True),
        (None, True, True),
    ],
)
def test_settle_evidence_returns_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int | None,
    complete: bool,
    expect_incomplete_log: bool,
    tmp_path: Path,
) -> None:
    """settle_evidence returns the cleanup tuple without raising across the
    (incomplete + leader-confirmed) and (complete + leader-unconfirmed) cases."""
    owner = _spawn_owner(monkeypatch, tmp_path)
    result_stub = _process_kill.ProcessCleanupResult(
        root_pid=owner.pid,
        access_denied_pids=(1234,) if not complete else (),
        observation_complete=complete,
    )
    monkeypatch.setattr(owner, "cleanup", lambda timeout: (returncode, result_stub))

    with structlog.testing.capture_logs() as logs:
        actual_returncode, actual_result = owner.settle_evidence()

    assert actual_returncode == returncode
    assert actual_result is result_stub
    incomplete_entries = [e for e in logs if e.get("event") == "owned_group_cleanup_incomplete"]
    if expect_incomplete_log:
        assert incomplete_entries
        if returncode is None:
            assert all(e.get("returncode_confirmed") is False for e in incomplete_entries)
    else:
        assert not incomplete_entries


def test_subprocess_result_carries_cleanup_evidence_field() -> None:
    from autoskillit.core import SubprocessResult, TerminationReason

    evidence = _process_kill.ProcessCleanupResult(root_pid=101, observation_complete=True)

    result = SubprocessResult(
        returncode=0,
        stdout="",
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=101,
        cleanup_evidence=evidence,
    )

    assert result.cleanup_evidence is evidence
