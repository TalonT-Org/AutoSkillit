"""Tests for _watch_child_activity coroutine and deadline extension behavior."""

from __future__ import annotations

import functools
import time

import anyio
import pytest

from autoskillit.core import BackendEventKind, OperationLiveness, OperationStatus
from autoskillit.core.types import SessionEvent, SessionLivenessSpec
from autoskillit.execution.process._liveness_supervisor import ProcessLivenessSupervisor
from autoskillit.execution.process._process_race import _watch_child_activity

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


@pytest.mark.anyio
async def test_extends_deadline_when_children_active(monkeypatch) -> None:
    """Deadline is extended when _has_active_child_processes returns True."""
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_child_processes",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_api_connection",
        lambda pid: False,
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
    """Deadline is NOT extended when both probes return False."""
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_child_processes",
        lambda pid: False,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_api_connection",
        lambda pid: False,
    )
    trigger = anyio.Event()
    scope_ref: list[anyio.CancelScope | None] = [None]
    original_deadline_ref: list[float] = []

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            functools.partial(
                _watch_child_activity, 1, scope_ref, 7200.0, trigger, 0.05, marker_dir=None
            )
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
        "autoskillit.execution.process._process_race._has_active_child_processes",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_api_connection",
        lambda pid: False,
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
async def test_operation_deadline_clamps_child_activity_extension(monkeypatch) -> None:
    poll_interval = 0.02
    operation_deadline = 0.08
    probe_times: list[float] = []

    def _active_child_processes(_pid: int) -> bool:
        probe_times.append(anyio.current_time())
        return True

    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_child_processes",
        _active_child_processes,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_api_connection",
        lambda pid: False,
    )
    supervisor = ProcessLivenessSupervisor(
        spec=SessionLivenessSpec(
            stdout_idle_timeout_sec=600.0,
            stale_threshold_sec=1200.0,
            operation_deadline_sec=operation_deadline,
            mcp_tool_timeout_sec=14364.0,
            wall_timeout_sec=7200.0,
            explicit_idle_disabled=False,
            caller_session_id="caller-1",
        )
    )
    started_anyio = anyio.current_time()
    started_monotonic = time.monotonic()
    supervisor.publish_event(
        SessionEvent(
            kind=BackendEventKind.IGNORED,
            is_terminal=False,
            has_marker=False,
            operation_liveness=OperationLiveness(
                operation_id="call-1",
                item_type="mcp_tool_call",
                status=OperationStatus.STARTED,
                started_monotonic=started_monotonic,
            ),
        )
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
                poll_interval,
                liveness_supervisor=supervisor,
            )
        )
        with anyio.move_on_after(0.03) as scope:
            scope_ref[0] = scope
            original_deadline_ref.append(scope.deadline)
            await anyio.sleep(0.075)
            trigger.set()
        tg.cancel_scope.cancel()

    assert scope_ref[0] is not None
    assert len(probe_times) >= 3
    unclamped_deadline = probe_times[-1] + poll_interval * 2
    operation_cap = started_anyio + operation_deadline
    assert scope_ref[0].deadline > original_deadline_ref[0]
    assert scope_ref[0].deadline <= operation_cap + 0.01
    assert scope_ref[0].deadline < unclamped_deadline - 0.01


@pytest.mark.anyio
async def test_terminates_on_trigger(monkeypatch) -> None:
    """Watcher exits cleanly when trigger fires immediately."""
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_child_processes",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_api_connection",
        lambda pid: True,
    )
    trigger = anyio.Event()
    scope_ref: list[anyio.CancelScope | None] = [None]

    trigger.set()

    with anyio.fail_after(2.0):
        await _watch_child_activity(1, scope_ref, 7200.0, trigger, 0.05)


@pytest.mark.anyio
async def test_api_connection_also_extends(monkeypatch) -> None:
    """Deadline is extended when _has_active_api_connection returns True (children inactive)."""
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_child_processes",
        lambda pid: False,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_api_connection",
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
async def test_scope_ref_none_polling(monkeypatch) -> None:
    """Watcher polls harmlessly when scope_ref is None (before scope binding)."""
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_child_processes",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_api_connection",
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


@pytest.mark.anyio
async def test_extends_deadline_when_dispatch_marker_active(monkeypatch, tmp_path) -> None:
    """Deadline is extended when dispatch marker is active (other signals inactive)."""
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_child_processes",
        lambda pid: False,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_api_connection",
        lambda pid: False,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, **kw: True,
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
                marker_dir=tmp_path,
                session_id="test-sid",
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
async def test_no_extension_when_marker_inactive(monkeypatch, tmp_path) -> None:
    """Deadline is NOT extended when all three signals are inactive (fleet context)."""
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_child_processes",
        lambda pid: False,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_api_connection",
        lambda pid: False,
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._process_race._has_active_execution_marker",
        lambda marker_dir, **kw: False,
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
                marker_dir=tmp_path,
                session_id="test-sid",
            )
        )
        with anyio.move_on_after(2.0) as scope:
            scope_ref[0] = scope
            original_deadline_ref.append(scope.deadline)
            await anyio.sleep(0.5)
            trigger.set()
        tg.cancel_scope.cancel()

    assert scope_ref[0] is not None
    assert scope_ref[0].deadline == original_deadline_ref[0]


def test_marker_dir_threaded_from_run_managed_async() -> None:
    """run_managed_async threads marker_dir and session_id to _watch_child_activity."""
    import re
    from pathlib import Path

    init_source = Path("src/autoskillit/execution/process/__init__.py").read_text()

    pattern = (
        r"functools\.partial\(\s*_watch_child_activity,"
        r".*?marker_dir=marker_dir.*?session_id=session_id"
    )
    match = re.search(pattern, init_source, re.DOTALL)
    assert match is not None, (
        "run_managed_async does not thread marker_dir and session_id "
        "to _watch_child_activity via functools.partial"
    )
