"""Tests for _watch_child_activity coroutine and deadline extension behavior."""

from __future__ import annotations

import anyio
import pytest

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
        with anyio.move_on_after(2.0) as scope:
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
        tg.start_soon(_watch_child_activity, 1, scope_ref, 7200.0, trigger, 0.05)
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
        with anyio.move_on_after(2.0) as scope:
            scope_ref[0] = scope
            original_deadline_ref.append(scope.deadline)
            await anyio.sleep(0.8)
            trigger.set()
        tg.cancel_scope.cancel()

    assert scope_ref[0] is not None
    # Cap is original_deadline + 0.2 (max_extension_seconds)
    assert scope_ref[0].deadline <= original_deadline_ref[0] + 0.2 + 0.05


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
        with anyio.move_on_after(2.0) as scope:
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
