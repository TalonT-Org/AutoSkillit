"""Real-process coverage for fleet dispatch reaping."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psutil
import pytest

from autoskillit.core.runtime._linux_proc import read_boot_id, read_starttime_ticks
from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    read_state,
    reap_stale_dispatches,
    reap_stale_dispatches_async,
)
from autoskillit.fleet._liveness import is_dispatch_session_alive
from tests.fleet._reaper_test_support import _make_running_state

pytestmark = [
    pytest.mark.layer("fleet"),
    pytest.mark.medium,
    pytest.mark.feature("fleet"),
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: /proc filesystem required"),
]

_CHILD_CODE = """
import signal
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
ready = Path(sys.argv[2])

def handle_term(*_args):
    marker.write_text("received")
    raise SystemExit

signal.signal(signal.SIGTERM, handle_term)
ready.write_text("ready")
time.sleep(120)
"""


def _wait_for_file(path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        if process.poll() is not None:
            pytest.fail(f"child process exited before creating {path.name}")
        if time.monotonic() >= deadline:
            pytest.fail(f"child process did not create {path.name}")
        time.sleep(0.01)


def _wait_for_exit(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail("child process did not exit after reaping")


@contextmanager
def _owned_sleeping_child(
    tmp_path: Path, name: str
) -> Iterator[tuple[subprocess.Popen[bytes], Path]]:
    marker = tmp_path / f"{name}.sigterm"
    ready = tmp_path / f"{name}.ready"
    process = subprocess.Popen(
        [sys.executable, "-c", _CHILD_CODE, os.fspath(marker), os.fspath(ready)]
    )
    try:
        _wait_for_file(ready, process)
        yield process, marker
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _require_process_identity(process: subprocess.Popen[bytes]) -> tuple[str, int]:
    boot_id = read_boot_id()
    ticks = read_starttime_ticks(process.pid)
    if boot_id is None or ticks is None:
        pytest.skip("Linux process identity was unavailable")
    return boot_id, ticks


def _reaped_dispatch(state_path: Path):
    state = read_state(state_path)
    assert state is not None
    dispatch = state.dispatches[0]
    assert dispatch.status == DispatchStatus.INTERRUPTED
    assert dispatch.reason == "reaped_orphan"
    assert dispatch.reaper_reason == "reaped_orphan"
    assert dispatch.ended_at is not None
    return dispatch


def _dispatch_outcome(state_path: Path) -> tuple[DispatchStatus, str, str, float | None]:
    state = read_state(state_path)
    assert state is not None
    dispatch = state.dispatches[0]
    return dispatch.status, dispatch.reason, dispatch.reaper_reason, dispatch.ended_at


def test_reap_terminates_identified_real_child(tmp_path: Path) -> None:
    with _owned_sleeping_child(tmp_path, "orphan") as (process, marker):
        boot_id, ticks = _require_process_identity(process)
        state_path = _make_running_state(
            tmp_path,
            dispatched_pid=process.pid,
            dispatched_boot_id=boot_id,
            dispatched_starttime_ticks=ticks,
        )

        reap_stale_dispatches(state_path, min_reap_age_seconds=0.0)

        _wait_for_exit(process)
        assert marker.exists()
        _reaped_dispatch(state_path)


def test_reap_skip_keeps_real_child_and_state_unchanged(tmp_path: Path) -> None:
    with _owned_sleeping_child(tmp_path, "skipped") as (process, marker):
        boot_id, ticks = _require_process_identity(process)
        state_path = _make_running_state(
            tmp_path,
            dispatch_id="skip-me",
            dispatched_pid=process.pid,
            dispatched_boot_id=boot_id,
            dispatched_starttime_ticks=ticks,
        )
        original_outcome = _dispatch_outcome(state_path)

        reap_stale_dispatches(
            state_path,
            skip_dispatch_ids=frozenset({"skip-me"}),
            min_reap_age_seconds=0.0,
        )

        assert process.poll() is None
        assert not marker.exists()
        assert _dispatch_outcome(state_path) == original_outcome


@pytest.mark.anyio
async def test_async_reap_forwards_skip_set_to_real_children(tmp_path: Path) -> None:
    state_dir_a = tmp_path / "a"
    state_dir_b = tmp_path / "b"
    state_dir_a.mkdir()
    state_dir_b.mkdir()
    with (
        _owned_sleeping_child(tmp_path, "a") as (process_a, marker_a),
        _owned_sleeping_child(tmp_path, "b") as (process_b, marker_b),
    ):
        boot_id_a, ticks_a = _require_process_identity(process_a)
        boot_id_b, ticks_b = _require_process_identity(process_b)
        state_path_a = _make_running_state(
            state_dir_a,
            dispatch_id="a",
            dispatched_pid=process_a.pid,
            dispatched_boot_id=boot_id_a,
            dispatched_starttime_ticks=ticks_a,
        )
        state_path_b = _make_running_state(
            state_dir_b,
            dispatch_id="b",
            dispatched_pid=process_b.pid,
            dispatched_boot_id=boot_id_b,
            dispatched_starttime_ticks=ticks_b,
        )
        original_b_outcome = _dispatch_outcome(state_path_b)

        await reap_stale_dispatches_async(
            [state_path_a, state_path_b],
            skip_dispatch_ids=frozenset({"b"}),
            min_reap_age_seconds=0.0,
        )

        _wait_for_exit(process_a)
        assert marker_a.exists()
        _reaped_dispatch(state_path_a)
        assert process_b.poll() is None
        assert not marker_b.exists()
        assert _dispatch_outcome(state_path_b) == original_b_outcome


def test_degraded_identity_is_not_live_but_create_time_fallback_reaps(tmp_path: Path) -> None:
    with _owned_sleeping_child(tmp_path, "create-time") as (process, marker):
        degraded_record = DispatchRecord(
            name="degraded",
            dispatched_pid=process.pid,
            dispatched_boot_id="",
            dispatched_starttime_ticks=0,
        )
        assert not is_dispatch_session_alive(degraded_record)
        state_path = _make_running_state(
            tmp_path,
            dispatched_pid=process.pid,
            dispatched_boot_id="",
            dispatched_starttime_ticks=0,
            dispatched_create_time=psutil.Process(process.pid).create_time(),
        )

        reap_stale_dispatches(state_path, min_reap_age_seconds=0.0)

        _wait_for_exit(process)
        assert marker.exists()
        _reaped_dispatch(state_path)
