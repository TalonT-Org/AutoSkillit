"""T2: is_session_alive generalized liveness."""

from __future__ import annotations

import os

import pytest

from autoskillit.core.runtime._linux_proc import (
    is_session_alive,
    read_boot_id,
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
