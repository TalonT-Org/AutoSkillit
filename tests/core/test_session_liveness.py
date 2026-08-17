"""T2: is_session_alive generalized liveness."""

from __future__ import annotations

import os
import time

import pytest

from autoskillit.core.runtime._linux_proc import (
    is_pid_zombie,
    is_session_alive,
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
    finally:
        os.waitpid(child_pid, 0)
