import os

import pytest

from autoskillit.core.runtime._linux_proc import read_boot_id, read_starttime_ticks
from autoskillit.fleet import DispatchRecord
from autoskillit.fleet._liveness import is_dispatch_session_alive

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestIsDispatchSessionAlive:
    def test_unstarted_dispatch_not_alive(self) -> None:
        record = DispatchRecord(name="test")  # dispatched_pid defaults to 0
        assert not is_dispatch_session_alive(record)

    def test_different_boot_id_not_alive(self) -> None:
        record = DispatchRecord(
            name="test",
            dispatched_pid=os.getpid(),
            dispatched_boot_id="different-boot-id-xyz",
            dispatched_starttime_ticks=999,
        )
        assert not is_dispatch_session_alive(record)

    def test_nonexistent_pid_not_alive(self) -> None:
        boot_id = read_boot_id()
        if boot_id is None:
            pytest.skip("Not on Linux")
        record = DispatchRecord(
            name="test",
            dispatched_pid=999999999,
            dispatched_boot_id=boot_id,
            dispatched_starttime_ticks=999,
        )
        assert not is_dispatch_session_alive(record)

    def test_current_process_is_alive(self) -> None:
        pid = os.getpid()
        ticks = read_starttime_ticks(pid)
        boot_id = read_boot_id()
        if ticks is None or boot_id is None:
            pytest.skip("Not on Linux")
        record = DispatchRecord(
            name="test",
            dispatched_pid=pid,
            dispatched_boot_id=boot_id,
            dispatched_starttime_ticks=ticks,
        )
        assert is_dispatch_session_alive(record)

    def test_ticks_mismatch_not_alive(self) -> None:
        pid = os.getpid()
        boot_id = read_boot_id()
        if boot_id is None:
            pytest.skip("Not on Linux")
        record = DispatchRecord(
            name="test",
            dispatched_pid=pid,
            dispatched_boot_id=boot_id,
            dispatched_starttime_ticks=-1,
        )
        assert not is_dispatch_session_alive(record)

    def test_missing_boot_id_on_record_not_alive(self) -> None:
        record = DispatchRecord(
            name="test",
            dispatched_pid=os.getpid(),
            dispatched_boot_id="",
            dispatched_starttime_ticks=999,
        )
        assert not is_dispatch_session_alive(record)

    def test_delegates_to_core_is_session_alive(self) -> None:
        from unittest.mock import patch

        record = DispatchRecord(
            name="test",
            dispatched_pid=42,
            dispatched_boot_id="boot-id",
            dispatched_starttime_ticks=100,
        )
        with patch("autoskillit.fleet._liveness.is_session_alive", return_value=True) as mock:
            result = is_dispatch_session_alive(record)
        mock.assert_called_once_with(42, "boot-id", 100)
        assert result is True
