"""Termination decision and execution helpers for managed subprocesses.

Provides `decide_termination_action` — the pure decision function that
maps race signals to a `TerminationAction` (deliberately free of anyio
and I/O so it can be tested as a pure decision table) — and
`execute_termination_action`, the sole authorized caller of
`async_kill_process_tree` for `run_managed_async`. Test-enforced.
"""

from __future__ import annotations

from pathlib import Path
from typing import assert_never

import anyio
import structlog

from autoskillit.core import (
    KillReason,
    ProcessCleanupResult,
    TerminationAction,
    TerminationReason,
)
from autoskillit.execution.process._process_kill import (
    OwnedProcessGroup,
    ProcessObservationSnapshot,
)
from autoskillit.execution.process._process_monitor import (
    _has_active_api_connection,
    _has_active_child_processes,
    _has_active_execution_marker,
)


def decide_termination_action(
    termination: TerminationReason,
    *,
    timeout_fired: bool,
    process_exited: bool,
    pending_task_ids: tuple[str, ...] = (),
    schedule_wakeup_violation: bool = False,
    completion_ceiling_expired: bool = False,
) -> TerminationAction:
    """Pure decision function: maps race signals to a TerminationAction.

    Priority:
    1. timeout_fired → IMMEDIATE_KILL (always overrides)
    2. process_exited → NO_KILL (process already gone, no signal needed)
    3. termination-reason dispatch:
       - COMPLETED: channel won but process alive → DRAIN_THEN_KILL_IF_ALIVE
       - NATURAL_EXIT: fallback case → NO_KILL
       - IDLE_STALL / STALE / TIMED_OUT: infra kill → IMMEDIATE_KILL

    The function is deliberately free of anyio and I/O so it can be tested
    as a pure decision table without any async or process infrastructure.
    """
    if timeout_fired:
        return TerminationAction.IMMEDIATE_KILL
    if process_exited and (
        pending_task_ids or schedule_wakeup_violation or completion_ceiling_expired
    ):
        return TerminationAction.IMMEDIATE_KILL
    if process_exited:
        return TerminationAction.NO_KILL
    match termination:
        case TerminationReason.NATURAL_EXIT | TerminationReason.SIGNAL_DEATH:
            return TerminationAction.NO_KILL
        case TerminationReason.COMPLETED:
            return TerminationAction.DRAIN_THEN_KILL_IF_ALIVE
        case (
            TerminationReason.IDLE_STALL
            | TerminationReason.STALE
            | TerminationReason.TIMED_OUT
            | TerminationReason.HEALTH_INSPECTOR
        ):
            return TerminationAction.IMMEDIATE_KILL
        case _ as unreachable:
            assert_never(unreachable)


async def execute_termination_action(
    action: TerminationAction,
    *,
    owner: OwnedProcessGroup,
    process_exited_event: anyio.Event,
    grace_seconds: float,
    proc_log: structlog.BoundLogger,
    pid: int | None = None,
    marker_dir: Path | None = None,
    session_id: str | None = None,
    child_deferral_ceiling: float = 0.0,
    process_observation_snapshot: ProcessObservationSnapshot | None = None,
) -> tuple[KillReason, int, ProcessCleanupResult]:
    """Single authorized executor for all kill decisions in run_managed_async.

    This is the sole managed-async authority for drain, signal, settlement, and reap.

    On the DRAIN_THEN_KILL_IF_ALIVE path, when *pid* is provided and
    *child_deferral_ceiling* > 0, the kill is deferred (bounded by the ceiling)
    while child processes, an API connection, or an execution marker indicate
    the subagent is still doing active work — mirroring the stale-kill
    suppression pattern in _session_log_monitor.

    Returns the kill reason, authoritative final return code, and cleanup evidence.
    """
    if process_observation_snapshot is not None:
        owner.merge_snapshot(process_observation_snapshot)
    match action:
        case TerminationAction.NO_KILL:
            kill_reason = KillReason.NATURAL_EXIT
        case TerminationAction.DRAIN_THEN_KILL_IF_ALIVE:
            with anyio.move_on_after(grace_seconds):
                await process_exited_event.wait()
            if owner.returncode is not None:
                proc_log.debug("natural_exit_after_drain", returncode=owner.returncode)
                kill_reason = KillReason.NATURAL_EXIT
                returncode, cleanup = await anyio.to_thread.run_sync(
                    owner.settle, abandon_on_cancel=False
                )
                return kill_reason, returncode, cleanup
            # Child-liveness deferral: same pattern as _session_log_monitor stale-kill suppression
            if pid is not None and child_deferral_ceiling > 0:
                deferral_start = anyio.current_time()
                _poll_interval = 2.0
                while (anyio.current_time() - deferral_start) < child_deferral_ceiling:
                    if owner.returncode is not None:
                        proc_log.debug("natural_exit_during_deferral", returncode=owner.returncode)
                        kill_reason = KillReason.NATURAL_EXIT
                        returncode, cleanup = await anyio.to_thread.run_sync(
                            owner.settle, abandon_on_cancel=False
                        )
                        return kill_reason, returncode, cleanup
                    active = (
                        _has_active_child_processes(pid)
                        or _has_active_api_connection(pid)
                        or (
                            marker_dir is not None
                            and _has_active_execution_marker(marker_dir, session_id=session_id)
                        )
                    )
                    if not active:
                        proc_log.debug("no_active_children_proceeding_to_kill")
                        break
                    proc_log.debug(
                        "child_liveness_deferral",
                        elapsed=anyio.current_time() - deferral_start,
                        ceiling=child_deferral_ceiling,
                    )
                    await anyio.sleep(_poll_interval)
            proc_log.debug("grace_expired_killing", grace_seconds=grace_seconds)
            kill_reason = KillReason.KILL_AFTER_COMPLETION
        case TerminationAction.IMMEDIATE_KILL:
            if pid is not None and _has_active_child_processes(pid):
                proc_log.warning(
                    "immediate_kill_with_active_children",
                    pid=pid,
                )
            kill_reason = KillReason.INFRA_KILL
        case _ as unreachable:
            assert_never(unreachable)
    returncode, cleanup = await anyio.to_thread.run_sync(owner.settle, abandon_on_cancel=False)
    return kill_reason, returncode, cleanup


__all__ = ["decide_termination_action", "execute_termination_action"]
