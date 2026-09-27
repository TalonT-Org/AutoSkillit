"""Real-process coverage for fleet dispatch reaping."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest
import structlog.testing

import autoskillit.fleet._dispatch_reaper as dispatch_reaper
from autoskillit.core import OWNER_SCOPE_DIR_ENV_VAR, OWNER_SCOPE_ENV_VAR
from autoskillit.core.runtime._linux_proc import read_boot_id, read_starttime_ticks
from autoskillit.execution import (
    OwnedProcessGroup,
    TetherSpec,
    default_tether_dir,
    spawn_owned_process,
)
from autoskillit.execution.process import is_owner_scope_sealed, new_dispatch_owner_scope_token
from autoskillit.fleet import (
    DispatchRecord,
    DispatchStatus,
    read_state,
    reap_stale_dispatches,
    reap_stale_dispatches_async,
)
from autoskillit.fleet._liveness import is_dispatch_session_alive
from tests.conftest import production_interpreter_env
from tests.fleet._reaper_test_support import BOOT_ID, make_running_state, write_dispatch_heartbeat

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
        [sys.executable, "-c", _CHILD_CODE, os.fspath(marker), os.fspath(ready)],
        env=production_interpreter_env(),
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


def _reaped_dispatch(state_path: Path) -> DispatchRecord:
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


def _wait_for_pid_gone(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(pid), f"pid {pid} should be dead"


def _dead_pid(tmp_path: Path) -> int:
    """Spawn and fully reap a real child, returning its now-dead pid."""
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"], env=production_interpreter_env(), cwd=tmp_path
    )
    process.wait(timeout=5)
    return process.pid


@contextmanager
def _scoped_owned_child(
    dispatch_id: str, tether_dir: Path
) -> Iterator[tuple[OwnedProcessGroup, str]]:
    """Spawn a real owned sleeper registered under a fresh dispatch owner-scope token."""
    token = new_dispatch_owner_scope_token(dispatch_id)
    env = production_interpreter_env()
    env[OWNER_SCOPE_ENV_VAR] = token
    env[OWNER_SCOPE_DIR_ENV_VAR] = str(tether_dir)
    owner = spawn_owned_process(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
        env=env,
        tether=TetherSpec(origin="test", ceiling_seconds=60.0),
    )
    try:
        yield owner, token
    finally:
        process = owner.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_reap_terminates_identified_real_child(tmp_path: Path) -> None:
    with _owned_sleeping_child(tmp_path, "orphan") as (process, marker):
        boot_id, ticks = _require_process_identity(process)
        state_path = make_running_state(
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
        state_path = make_running_state(
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
        state_path_a = make_running_state(
            state_dir_a,
            dispatch_id="a",
            dispatched_pid=process_a.pid,
            dispatched_boot_id=boot_id_a,
            dispatched_starttime_ticks=ticks_a,
        )
        state_path_b = make_running_state(
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
        try:
            child_create_time = psutil.Process(process.pid).create_time()
        except psutil.NoSuchProcess:
            pytest.skip("child process exited before create_time could be read")
        state_path = make_running_state(
            tmp_path,
            dispatched_pid=process.pid,
            dispatched_boot_id="",
            dispatched_starttime_ticks=0,
            dispatched_create_time=child_create_time,
        )

        reap_stale_dispatches(state_path, min_reap_age_seconds=0.0)

        _wait_for_exit(process)
        assert marker.exists()
        _reaped_dispatch(state_path)


def test_reap_settles_scoped_owner_scope_for_dead_pid_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "logs"))
    tether_dir = default_tether_dir()
    boot_id = read_boot_id()
    if boot_id is None:
        pytest.skip("Linux process identity was unavailable")

    state_path = make_running_state(
        tmp_path,
        dispatch_id="owner-dead",
        dispatched_pid=_dead_pid(tmp_path),
        dispatched_boot_id=boot_id,
    )

    with _scoped_owned_child("owner-dead", tether_dir) as (owner, token):
        with structlog.testing.capture_logs() as cap_logs:
            reap_stale_dispatches(state_path, min_reap_age_seconds=0.0)

        _wait_for_pid_gone(owner.pid)
        assert is_owner_scope_sealed(tether_dir, token)

    settled_events = [e for e in cap_logs if "[SETTLED]" in e.get("event", "")]
    assert len(settled_events) == 1
    assert token in settled_events[0]["event"]

    status, reason, reaper_reason, ended_at = _dispatch_outcome(state_path)
    assert status == DispatchStatus.INTERRUPTED
    assert reason == "reaped_dead_pid"
    assert reaper_reason == "reaped_dead_pid"
    assert ended_at is not None


def test_reap_dry_run_would_settle_owner_scope_and_leaves_state_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "logs"))
    tether_dir = default_tether_dir()
    boot_id = read_boot_id()
    if boot_id is None:
        pytest.skip("Linux process identity was unavailable")

    state_path = make_running_state(
        tmp_path,
        dispatch_id="owner-dry",
        dispatched_pid=_dead_pid(tmp_path),
        dispatched_boot_id=boot_id,
    )
    original_text = state_path.read_text()

    with _scoped_owned_child("owner-dry", tether_dir) as (owner, token):
        with structlog.testing.capture_logs() as cap_logs:
            reap_stale_dispatches(state_path, dry_run=True, min_reap_age_seconds=0.0)

        assert owner.process.poll() is None
        assert not is_owner_scope_sealed(tether_dir, token)

    assert state_path.read_text() == original_text
    would_settle_events = [e for e in cap_logs if "[WOULD SETTLE]" in e.get("event", "")]
    assert len(would_settle_events) == 1
    assert token in would_settle_events[0]["event"]


def test_reap_fresh_heartbeat_blocks_settle_and_dead_pid_marking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "logs"))
    tether_dir = default_tether_dir()
    boot_id = read_boot_id()
    if boot_id is None:
        pytest.skip("Linux process identity was unavailable")

    state_path = make_running_state(
        tmp_path,
        dispatch_id="owner-hb",
        dispatched_pid=_dead_pid(tmp_path),
        dispatched_boot_id=boot_id,
    )
    original_text = state_path.read_text()
    write_dispatch_heartbeat(tmp_path, "owner-hb")

    with _scoped_owned_child("owner-hb", tether_dir) as (owner, token):
        reap_stale_dispatches(state_path, min_reap_age_seconds=0.0)

        assert owner.process.poll() is None
        assert not is_owner_scope_sealed(tether_dir, token)

    assert state_path.read_text() == original_text


def test_reap_fresh_heartbeat_blocks_dead_pid_via_identity_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh heartbeat must gate the record before the identity pipeline ever runs.

    Mocks the identity-``None`` disposition (``_confirm_dispatch_pid_identity``
    raising ``NoSuchProcess``) that would otherwise mark the dispatch dead, and
    asserts the primitives it depends on are never even called.
    """
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "logs"))
    tether_dir = default_tether_dir()

    state_path = make_running_state(
        tmp_path,
        dispatch_id="owner-none",
        dispatched_pid=12345,
        dispatched_starttime_ticks=0,
        dispatched_create_time=1000000.5,
        dispatched_boot_id=BOOT_ID,
    )
    original_text = state_path.read_text()
    write_dispatch_heartbeat(tmp_path, "owner-none")

    with _scoped_owned_child("owner-none", tether_dir) as (owner, token):
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists") as mock_pid_exists,
            patch("autoskillit.fleet._dispatch_reaper.psutil.Process") as mock_proc_cls,
            patch.object(dispatch_reaper, "read_boot_id", return_value=BOOT_ID),
            patch.object(dispatch_reaper, "kill_process_tree") as mock_kill,
        ):
            mock_proc_cls.return_value.create_time.side_effect = psutil.NoSuchProcess(12345)
            reap_stale_dispatches(state_path, min_reap_age_seconds=0.0)

        mock_pid_exists.assert_not_called()
        mock_proc_cls.assert_not_called()
        mock_kill.assert_not_called()
        assert owner.process.poll() is None
        assert not is_owner_scope_sealed(tether_dir, token)

    assert state_path.read_text() == original_text


def test_reap_fresh_heartbeat_blocks_pid_recycled_via_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh heartbeat must gate the record before a recycled-pid identity check runs."""
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "logs"))
    tether_dir = default_tether_dir()

    state_path = make_running_state(
        tmp_path,
        dispatch_id="owner-recycled",
        dispatched_pid=12345,
        dispatched_starttime_ticks=1000,
        dispatched_boot_id=BOOT_ID,
    )
    original_text = state_path.read_text()
    write_dispatch_heartbeat(tmp_path, "owner-recycled")

    with _scoped_owned_child("owner-recycled", tether_dir) as (owner, token):
        with (
            patch("autoskillit.fleet._dispatch_reaper.psutil.pid_exists") as mock_pid_exists,
            patch.object(dispatch_reaper, "read_starttime_ticks", return_value=9999) as mock_ticks,
            patch.object(dispatch_reaper, "read_boot_id", return_value=BOOT_ID),
            patch.object(dispatch_reaper, "kill_process_tree") as mock_kill,
        ):
            reap_stale_dispatches(state_path, min_reap_age_seconds=0.0)

        mock_pid_exists.assert_not_called()
        mock_ticks.assert_not_called()
        mock_kill.assert_not_called()
        assert owner.process.poll() is None
        assert not is_owner_scope_sealed(tether_dir, token)

    assert state_path.read_text() == original_text
