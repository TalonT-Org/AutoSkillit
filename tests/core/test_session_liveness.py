"""T2: is_session_alive generalized liveness."""

from __future__ import annotations

import ctypes
import os
import time

import pytest

import autoskillit.core.runtime._linux_proc as subject
from autoskillit.core.runtime._linux_proc import (
    is_pid_zombie,
    is_session_alive,
    owner_liveness,
    read_boot_id,
    read_process_state,
    read_starttime_ticks,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestIsSessionAlive:
    def test_zero_pid_not_alive(self) -> None:
        assert is_session_alive(0, "some-boot-id", 12345) is False

    def test_empty_boot_id_not_alive(self) -> None:
        assert is_session_alive(os.getpid(), "", 12345) is False

    def test_different_boot_id_not_alive(self) -> None:
        boot_id = read_boot_id()
        if boot_id is None:
            pytest.skip("Not on Linux")
        assert is_session_alive(os.getpid(), "wrong-boot-id-xxx", 12345) is False

    def test_nonexistent_pid_not_alive(self) -> None:
        boot_id = read_boot_id()
        if boot_id is None:
            pytest.skip("Not on Linux")
        assert is_session_alive(999999999, boot_id, 12345) is False

    def test_ticks_mismatch_not_alive(self) -> None:
        boot_id = read_boot_id()
        if boot_id is None:
            pytest.skip("Not on Linux")
        assert is_session_alive(os.getpid(), boot_id, -1) is False

    def test_current_process_is_alive(self) -> None:
        boot_id = read_boot_id()
        ticks = read_starttime_ticks(os.getpid())
        if boot_id is None or ticks is None:
            pytest.skip("Not on Linux")
        assert is_session_alive(os.getpid(), boot_id, ticks) is True


def test_read_process_state_and_is_pid_zombie() -> None:
    boot_id = read_boot_id()
    if boot_id is None:
        pytest.skip("Not on Linux")
    child_pid = os.fork()
    if child_pid == 0:
        os._exit(0)
    try:
        deadline = time.monotonic() + 2.0
        state = read_process_state(child_pid)
        while state != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
            state = read_process_state(child_pid)
        assert state == "Z"
        assert is_pid_zombie(child_pid) is True
    finally:
        os.waitpid(child_pid, 0)


def test_is_session_alive_returns_false_for_zombie() -> None:
    boot_id = read_boot_id()
    if boot_id is None:
        pytest.skip("Not on Linux")
    child_pid = os.fork()
    if child_pid == 0:
        os._exit(0)
    try:
        ticks = read_starttime_ticks(child_pid)
        deadline = time.monotonic() + 2.0
        while read_process_state(child_pid) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ticks is not None
        assert is_session_alive(child_pid, boot_id, ticks) is False
        assert owner_liveness(child_pid, boot_id, ticks) is False
    finally:
        os.waitpid(child_pid, 0)


def test_owner_liveness_refuses_unreadable_linux_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject.sys, "platform", "linux")
    monkeypatch.setattr(subject, "read_boot_id", lambda **_kwargs: "boot")
    monkeypatch.setattr(subject, "read_starttime_ticks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(subject.os, "kill", lambda *_args: None)

    assert owner_liveness(123, "boot", 10) is None


@pytest.mark.parametrize(
    ("status", "current_boot_id", "starttime_ticks", "expected"),
    [
        pytest.param(0, "1:000002", 2_000_003, True, id="live-matching"),
        pytest.param(
            subject._DARWIN_ZOMBIE_STATUS,
            "1:000002",
            2_000_003,
            False,
            id="zombie",
        ),
        pytest.param(0, "1:000002", 2_000_004, False, id="changed-starttime"),
        pytest.param(0, "9:000009", 2_000_003, False, id="changed-boot"),
        pytest.param(None, "1:000002", 2_000_003, None, id="unavailable"),
    ],
)
def test_darwin_owner_liveness_uses_boot_time_and_process_start(
    monkeypatch: pytest.MonkeyPatch,
    status: int | None,
    current_boot_id: str,
    starttime_ticks: int,
    expected: bool | None,
) -> None:
    info = None
    if status is not None:
        info = subject._DarwinProcBsdInfo()
        info.pbi_pid = 123
        info.pbi_status = status
        info.pbi_start_tvsec = 2
        info.pbi_start_tvusec = 3
    monkeypatch.setattr(subject.sys, "platform", "darwin")
    monkeypatch.setattr(subject, "_darwin_boot_id", lambda: current_boot_id)
    monkeypatch.setattr(subject, "_read_darwin_proc_bsd_info", lambda _pid: info)
    monkeypatch.setattr(subject.os, "kill", lambda *_args: None)

    assert owner_liveness(123, "1:000002", starttime_ticks) is expected


def test_darwin_boot_time_refuses_short_sysctl_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    class Library:
        sysctlbyname: object

    def short_reply(
        _name: bytes,
        _value: object,
        size: object,
        _new_value: object,
        _new_value_size: int,
    ) -> int:
        ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = 1
        return 0

    library = Library()
    library.sysctlbyname = short_reply
    monkeypatch.setattr(subject.ctypes, "CDLL", lambda *_args, **_kwargs: library)

    assert subject._darwin_boot_time() is None


def test_darwin_process_snapshot_refuses_short_proc_pidinfo_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Library:
        proc_pidinfo: object

    def short_reply(
        _pid: int,
        _flavor: int,
        _arg: int,
        _buffer: object,
        size: int,
    ) -> int:
        return size - 1

    library = Library()
    library.proc_pidinfo = short_reply
    monkeypatch.setattr(subject.ctypes, "CDLL", lambda *_args, **_kwargs: library)

    assert subject._read_darwin_proc_bsd_info(123) is None
