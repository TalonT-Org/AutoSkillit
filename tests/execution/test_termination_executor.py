"""Integration tests for the sole managed-async termination authority."""

from __future__ import annotations

import sys
from typing import cast

import anyio
import pytest
import structlog

from autoskillit.core import KillReason, TerminationAction
from autoskillit.execution.process import (
    RaceAccumulator,
    _watch_process,
    execute_termination_action,
    spawn_owned_process,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


async def _spawn(delay: float) -> object:
    return await anyio.to_thread.run_sync(
        lambda: spawn_owned_process(
            [sys.executable, "-c", f"import time; time.sleep({delay})"],
            start_new_session=True,
        )
    )


@pytest.mark.anyio
async def test_drain_allows_natural_exit_and_returns_cleanup_evidence() -> None:
    owner = await _spawn(0.1)
    acc = RaceAccumulator(process_observation_snapshot=owner.snapshot)
    trigger = anyio.Event()
    async with anyio.create_task_group() as tg:
        tg.start_soon(_watch_process, owner, acc, trigger)
        await trigger.wait()
        kill_reason, returncode, cleanup = await execute_termination_action(
            TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
            owner=owner,
            process_exited_event=acc.process_exited_event,
            grace_seconds=1.0,
            proc_log=structlog.get_logger().bind(pid=owner.pid),
            process_observation_snapshot=acc.process_observation_snapshot,
        )
        tg.cancel_scope.cancel()

    assert kill_reason is KillReason.NATURAL_EXIT
    assert returncode == 0
    assert cleanup.complete is True
    assert owner.process.returncode == 0


@pytest.mark.anyio
async def test_drain_escalates_through_owner_when_leader_stays_live() -> None:
    owner = await _spawn(30)

    kill_reason, returncode, cleanup = await execute_termination_action(
        TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
        owner=owner,
        process_exited_event=anyio.Event(),
        grace_seconds=0.01,
        proc_log=structlog.get_logger().bind(pid=owner.pid),
    )

    assert kill_reason is KillReason.KILL_AFTER_COMPLETION
    assert returncode < 0
    assert cleanup.complete is True


@pytest.mark.anyio
async def test_immediate_kill_skips_drain() -> None:
    class UnexpectedDrain:
        async def wait(self) -> None:
            pytest.fail("IMMEDIATE_KILL must not wait for process exit")

    owner = await _spawn(30)

    with anyio.fail_after(5):
        kill_reason, returncode, cleanup = await execute_termination_action(
            TerminationAction.IMMEDIATE_KILL,
            owner=owner,
            process_exited_event=cast(anyio.Event, UnexpectedDrain()),
            grace_seconds=30,
            proc_log=structlog.get_logger().bind(pid=owner.pid),
        )

    assert kill_reason is KillReason.INFRA_KILL
    assert returncode < 0
    assert cleanup.complete is True


@pytest.mark.anyio
async def test_no_kill_path_still_settles_and_reaps_natural_exit() -> None:
    owner = await _spawn(0.05)
    while await anyio.to_thread.run_sync(owner.observe_exit) is None:
        await anyio.sleep(0.01)

    kill_reason, returncode, cleanup = await execute_termination_action(
        TerminationAction.NO_KILL,
        owner=owner,
        process_exited_event=anyio.Event(),
        grace_seconds=0,
        proc_log=structlog.get_logger().bind(pid=owner.pid),
        process_observation_snapshot=owner.snapshot,
    )

    assert (kill_reason, returncode) == (KillReason.NATURAL_EXIT, 0)
    assert cleanup.complete is True


@pytest.mark.anyio
async def test_owner_is_carried_through_child_liveness_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _spawn(30)
    monkeypatch.setattr(
        "autoskillit.execution.process._has_active_child_processes", lambda _pid: False
    )
    monkeypatch.setattr(
        "autoskillit.execution.process._has_active_api_connection", lambda _pid: False
    )

    kill_reason, _returncode, cleanup = await execute_termination_action(
        TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
        owner=owner,
        process_exited_event=anyio.Event(),
        grace_seconds=0.01,
        proc_log=structlog.get_logger().bind(pid=owner.pid),
        pid=owner.pid,
        child_deferral_ceiling=1.0,
    )

    assert kill_reason is KillReason.KILL_AFTER_COMPLETION
    assert cleanup.complete is True
