import ast
import os
import sys
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from autoskillit.core.runtime._linux_proc import read_boot_id, read_starttime_ticks
from autoskillit.fleet import DispatchRecord
from autoskillit.fleet._liveness import is_dispatch_session_alive

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.small,
    pytest.mark.feature("fleet"),
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: /proc filesystem required"),
]


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
        pid = os.getpid()
        ticks = read_starttime_ticks(pid)
        if ticks is None:
            pytest.skip("Not on Linux")
        record = DispatchRecord(
            name="test",
            dispatched_pid=pid,
            dispatched_boot_id="",
            dispatched_starttime_ticks=ticks,
            dispatched_create_time=0.0,
            identity_degraded=True,
        )
        assert not is_dispatch_session_alive(record)


class TestLivenessReaperConsistency:
    def test_degraded_identity_with_create_time_is_alive(self) -> None:
        pid = os.getpid()
        create_time = psutil.Process(pid).create_time()
        record = DispatchRecord(
            name="test",
            dispatched_pid=pid,
            dispatched_boot_id="",
            dispatched_starttime_ticks=0,
            dispatched_create_time=create_time,
            identity_degraded=True,
        )
        assert is_dispatch_session_alive(record)

    def test_degraded_identity_without_create_time_is_dead(self) -> None:
        pid = os.getpid()
        record = DispatchRecord(
            name="test",
            dispatched_pid=pid,
            dispatched_boot_id="",
            dispatched_starttime_ticks=0,
            dispatched_create_time=0.0,
            identity_degraded=True,
        )
        assert not is_dispatch_session_alive(record)

    def test_degraded_identity_with_mismatched_create_time_is_dead(self) -> None:
        pid = os.getpid()
        record = DispatchRecord(
            name="test",
            dispatched_pid=pid,
            dispatched_boot_id="",
            dispatched_starttime_ticks=0,
            dispatched_create_time=1.0,
            identity_degraded=True,
        )
        assert not is_dispatch_session_alive(record)

    def test_empty_boot_id_with_valid_ticks_is_alive(self) -> None:
        pid = os.getpid()
        ticks = read_starttime_ticks(pid)
        if ticks is None:
            pytest.skip("Not on Linux")
        create_time = psutil.Process(pid).create_time()
        record = DispatchRecord(
            name="test",
            dispatched_pid=pid,
            dispatched_boot_id="",
            dispatched_starttime_ticks=ticks,
            dispatched_create_time=create_time,
        )
        assert is_dispatch_session_alive(record)

    def test_full_identity_with_proc_failure_falls_back_to_create_time(self) -> None:
        pid = os.getpid()
        boot_id = read_boot_id()
        if boot_id is None:
            pytest.skip("Not on Linux")
        ticks = read_starttime_ticks(pid)
        if ticks is None:
            pytest.skip("Not on Linux")
        create_time = psutil.Process(pid).create_time()
        record = DispatchRecord(
            name="test",
            dispatched_pid=pid,
            dispatched_boot_id=boot_id,
            dispatched_starttime_ticks=ticks,
            dispatched_create_time=create_time,
        )
        with patch(
            "autoskillit.fleet._liveness.read_starttime_ticks",
            return_value=None,
        ):
            assert is_dispatch_session_alive(record)

    def test_full_identity_with_ticks_mismatch_no_fallback(self) -> None:
        pid = os.getpid()
        boot_id = read_boot_id()
        if boot_id is None:
            pytest.skip("Not on Linux")
        create_time = psutil.Process(pid).create_time()
        record = DispatchRecord(
            name="test",
            dispatched_pid=pid,
            dispatched_boot_id=boot_id,
            dispatched_starttime_ticks=-1,
            dispatched_create_time=create_time,
        )
        assert not is_dispatch_session_alive(record)


def test_reaper_delegates_to_confirm_dispatch_identity() -> None:
    source = (Path(__file__).parents[2] / "src/autoskillit/fleet/_dispatch_reaper.py").read_text()
    tree = ast.parse(source)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "confirm_dispatch_identity" in calls
