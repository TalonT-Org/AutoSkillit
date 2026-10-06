"""Tests for _watch_child_activity coroutine and deadline extension behavior."""

from __future__ import annotations

import functools
import sys
import time

import anyio
import psutil
import pytest

import autoskillit.execution.process._race_watchers as _patch_process__race_watchers
from autoskillit.execution.process import run_managed_async
from autoskillit.execution.process._process_race import RaceAccumulator
from autoskillit.execution.process._race_watchers import (
    _enroll_child_activity_watcher,
    _watch_child_activity,
)
from tests.execution._process_helpers import ConnectedProcess

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


@pytest.mark.anyio
@pytest.mark.parametrize("terminal_after", [0.08, None], ids=["terminal", "active-cap"])
async def test_pending_task_extension_releases_on_terminal_and_respects_cap(
    monkeypatch, terminal_after: float | None
) -> None:
    monkeypatch.setattr(
        _patch_process__race_watchers,
        "_has_active_child_processes",
        lambda pid: False,
    )
    pending = [True]
    trigger = anyio.Event()
    original_deadline = anyio.current_time() + 0.03
    scope = anyio.CancelScope(deadline=original_deadline)
    scope_ref: list[anyio.CancelScope | None] = [scope]

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(
            functools.partial(
                _watch_child_activity,
                1,
                scope_ref,
                0.1,
                trigger,
                0.02,
                has_pending_tasks=lambda: pending[0],
            )
        )
        await anyio.sleep(terminal_after or 0.2)
        deadline_while_active = scope.deadline
        if terminal_after is not None:
            pending[0] = False
            await anyio.sleep(0.06)
            assert scope.deadline == deadline_while_active
        trigger.set()

    assert deadline_while_active > original_deadline
    assert scope.deadline <= original_deadline + 0.1


@pytest.mark.anyio
async def test_extends_deadline_when_children_active(monkeypatch) -> None:
    """Deadline is extended when _has_active_child_processes returns True."""
    monkeypatch.setattr(
        _patch_process__race_watchers,
        "_has_active_child_processes",
        lambda pid: True,
    )
    trigger = anyio.Event()
    scope_ref: list[anyio.CancelScope | None] = [None]
    original_deadline_ref: list[float] = []

    async with anyio.create_task_group() as tg:
        tg.start_soon(_watch_child_activity, 1, scope_ref, 7200.0, trigger, 0.05)
        with anyio.move_on_after(0.1) as scope:
            scope_ref[0] = scope
            original_deadline_ref.append(scope.deadline)
            await anyio.sleep(0.3)
            trigger.set()
        tg.cancel_scope.cancel()

    assert scope_ref[0] is not None
    assert scope_ref[0].deadline > original_deadline_ref[0]


@pytest.mark.anyio
async def test_no_extension_when_inactive(monkeypatch) -> None:
    """Deadline is not extended when no lease or child process is active."""
    monkeypatch.setattr(
        _patch_process__race_watchers,
        "_has_active_child_processes",
        lambda pid: False,
    )
    trigger = anyio.Event()
    scope_ref: list[anyio.CancelScope | None] = [None]
    original_deadline_ref: list[float] = []

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            functools.partial(_watch_child_activity, 1, scope_ref, 7200.0, trigger, 0.05)
        )
        with anyio.move_on_after(2.0) as scope:
            scope_ref[0] = scope
            original_deadline_ref.append(scope.deadline)
            await anyio.sleep(0.5)
            trigger.set()
        tg.cancel_scope.cancel()

    assert scope_ref[0] is not None
    assert scope_ref[0].deadline == original_deadline_ref[0]


@pytest.mark.anyio
async def test_max_extension_cap_enforced(monkeypatch) -> None:
    """Extension is capped at max_extension_seconds beyond original deadline."""
    monkeypatch.setattr(
        _patch_process__race_watchers,
        "_has_active_child_processes",
        lambda pid: True,
    )
    trigger = anyio.Event()
    scope_ref: list[anyio.CancelScope | None] = [None]
    original_deadline_ref: list[float] = []

    async with anyio.create_task_group() as tg:
        tg.start_soon(_watch_child_activity, 1, scope_ref, 0.2, trigger, 0.05)
        with anyio.move_on_after(0.1) as scope:
            scope_ref[0] = scope
            original_deadline_ref.append(scope.deadline)
            await anyio.sleep(0.8)
            trigger.set()
        tg.cancel_scope.cancel()

    assert scope_ref[0] is not None
    assert scope_ref[0].deadline <= original_deadline_ref[0] + 0.2


@pytest.mark.anyio
async def test_terminates_on_trigger(monkeypatch) -> None:
    """Watcher exits cleanly when trigger fires immediately."""
    monkeypatch.setattr(
        _patch_process__race_watchers,
        "_has_active_child_processes",
        lambda pid: True,
    )
    trigger = anyio.Event()
    scope_ref: list[anyio.CancelScope | None] = [None]

    trigger.set()

    with anyio.fail_after(2.0):
        await _watch_child_activity(1, scope_ref, 7200.0, trigger, 0.05)


@pytest.mark.anyio
async def test_network_connection_without_lease_does_not_extend_deadline(
    monkeypatch, tmp_path
) -> None:
    """An established port-443 connection does not extend a lease-free deadline."""

    channel = tmp_path / "leases"
    channel.mkdir()
    import autoskillit.execution.process._process_monitor as process_monitor

    monkeypatch.setattr(process_monitor.psutil, "Process", ConnectedProcess)
    monkeypatch.setattr(
        _patch_process__race_watchers,
        "_has_active_child_processes",
        lambda pid: False,
    )
    trigger = anyio.Event()
    scope_ref: list[anyio.CancelScope | None] = [None]
    original_deadline_ref: list[float] = []

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            functools.partial(
                _watch_child_activity,
                1,
                scope_ref,
                7200.0,
                trigger,
                0.05,
                operation_lease_dir=channel,
            )
        )
        with anyio.move_on_after(0.1) as scope:
            scope_ref[0] = scope
            original_deadline_ref.append(scope.deadline)
            await anyio.sleep(0.3)
            trigger.set()
        tg.cancel_scope.cancel()

    assert scope_ref[0] is not None
    assert scope_ref[0].deadline == original_deadline_ref[0]


@pytest.mark.anyio
async def test_operation_lease_extends_deadline(monkeypatch, tmp_path) -> None:
    from autoskillit.core import InFlightOperations, operation_lease

    monkeypatch.setattr(
        _patch_process__race_watchers,
        "_has_active_child_processes",
        lambda _pid: False,
    )
    async with operation_lease(
        tmp_path,
        operation="run_skill",
        not_after_epoch=time.time() + 60,
        registry=InFlightOperations(),
        heartbeat_interval=10000,
    ):
        trigger = anyio.Event()
        scope_ref: list[anyio.CancelScope | None] = [None]
        original_deadline_ref: list[float] = []

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                functools.partial(
                    _watch_child_activity,
                    1,
                    scope_ref,
                    7200.0,
                    trigger,
                    0.05,
                    operation_lease_dir=tmp_path,
                )
            )
            with anyio.move_on_after(0.1) as scope:
                scope_ref[0] = scope
                original_deadline_ref.append(scope.deadline)
                await anyio.sleep(0.3)
                trigger.set()
            tg.cancel_scope.cancel()

    assert scope_ref[0] is not None
    assert scope_ref[0].deadline > original_deadline_ref[0]


@pytest.mark.anyio
async def test_scope_ref_none_polling(monkeypatch) -> None:
    """Watcher polls harmlessly when scope_ref is None (before scope binding)."""
    monkeypatch.setattr(
        _patch_process__race_watchers,
        "_has_active_child_processes",
        lambda pid: True,
    )
    trigger = anyio.Event()
    scope_ref: list[anyio.CancelScope | None] = [None]  # intentionally empty

    with anyio.fail_after(2.0):
        # Run watcher with empty scope_ref for 3 poll cycles, then set scope and trigger
        async with anyio.create_task_group() as tg:
            tg.start_soon(_watch_child_activity, 1, scope_ref, 7200.0, trigger, 0.05)
            await anyio.sleep(0.2)
            with anyio.move_on_after(1.0) as scope:
                scope_ref[0] = scope
            trigger.set()
            tg.cancel_scope.cancel()


@pytest.mark.parametrize(
    ("enabled", "pid", "lifecycle"),
    [(True, 17, True), (True, 17, False), (False, 17, True), (True, None, True)],
)
def test_child_activity_watcher_enrollment(
    tmp_path, enabled: bool, pid: int | None, lifecycle: bool
) -> None:
    class CapturingTaskGroup:
        def __init__(self) -> None:
            self.scheduled: list[tuple[object, tuple[object, ...]]] = []

        def start_soon(self, func, *args) -> None:
            self.scheduled.append((func, args))

    tg = CapturingTaskGroup()

    async def watcher(*args, **kwargs) -> None:
        pass

    acc = RaceAccumulator()
    trigger = anyio.Event()
    scope_ref: list[anyio.CancelScope | None] = [None]
    _enroll_child_activity_watcher(
        tg,
        _watch_child_activity=watcher,
        enable_deadline_extension=enabled,
        observed_pid=pid,
        timeout_scope_ref=scope_ref,
        max_extension_seconds=90.0,
        trigger=trigger,
        operation_lease_dir=tmp_path,
        lifecycle_observation_enabled=lifecycle,
        acc=acc,
    )

    if not enabled or pid is None:
        assert tg.scheduled == []
        return

    assert len(tg.scheduled) == 1
    scheduled, args = tg.scheduled[0]
    assert args == ()
    assert isinstance(scheduled, functools.partial)
    assert scheduled.func is watcher
    assert scheduled.args == (pid, scope_ref, 90.0, trigger)
    assert scheduled.keywords["operation_lease_dir"] == tmp_path
    assert "marker_dir" not in scheduled.keywords
    assert "session_id" not in scheduled.keywords
    callback = scheduled.keywords["has_pending_tasks"]
    if lifecycle:
        assert callback.__self__ is acc
        assert callback.__func__ is RaceAccumulator.has_unresolved_obligations
    else:
        assert callback is None


def test_operation_lease_dir_threaded_from_race_watcher_wiring() -> None:
    """Race watcher wiring forwards the lease channel to child activity."""
    import re
    from pathlib import Path

    watcher_source = Path("src/autoskillit/execution/process/_race_watchers.py").read_text()

    pattern = (
        r"functools\.partial\(\s*_watch_child_activity,"
        r".*?operation_lease_dir=operation_lease_dir"
    )
    match = re.search(pattern, watcher_source, re.DOTALL)
    assert match is not None, (
        "race watcher wiring does not thread operation_lease_dir "
        "to _watch_child_activity via functools.partial"
    )


@pytest.mark.anyio
async def test_extension_cap_kills_real_process(tmp_path) -> None:
    """A real, continuously-active child hits max_extension_seconds and gets killed
    regardless of ongoing activity — closes the "kill leg never exercised" gap left by
    the scope-arithmetic tests above, which only assert against a fake pid=1."""
    script = tmp_path / "stay_active.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "time.sleep(30)\n"
    )

    start = time.monotonic()
    result = await run_managed_async(
        [sys.executable, str(script)],
        cwd=tmp_path,
        timeout=1.0,
        enable_deadline_extension=True,
        max_extension_seconds=1.5,
    )
    elapsed = time.monotonic() - start

    assert not psutil.pid_exists(result.pid)
    # The cap must actually bind — well under the child's own 30s sleep.
    assert elapsed < 15.0
