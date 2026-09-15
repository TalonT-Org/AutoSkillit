"""Watcher enrollment and completion race coordination for managed processes."""

from __future__ import annotations

import functools
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import anyio
import anyio.abc

from autoskillit.core import InspectorEvidence, get_logger
from autoskillit.execution.process._process_jsonl import fold_event_cursor
from autoskillit.execution.process._process_monitor import (
    _has_active_api_connection,
    _has_active_child_processes,
    _has_active_execution_marker,
)
from autoskillit.execution.process._process_race import RaceAccumulator

if TYPE_CHECKING:
    from autoskillit.core import InspectorCallback, StreamParser, SupportsDebug
    from autoskillit.execution.process._lifecycle.line_driver_tee import LineDriverSession
    from autoskillit.execution.process._lifecycle.owned_group import OwnedProcessGroup

logger = get_logger(__name__)

INSPECTOR_MAX_SECONDS: float = 60.0
CLEANUP_BUDGET_SECONDS: float = 15.0


def _package_evidence(
    stdout_path: Path,
    idle_seconds: float,
    execution_marker_present: bool,
) -> InspectorEvidence:
    return InspectorEvidence(
        idle_seconds=idle_seconds,
        stdout_path=str(stdout_path),
        jsonl_lines=(),
        execution_marker_present=execution_marker_present,
    )


async def _watch_stdout_idle(
    stdout_path: Path,
    idle_output_timeout: float,
    acc: RaceAccumulator,
    trigger: anyio.Event,
    _poll_interval: float = 5.0,
    *,
    marker_dir: Path | None = None,
    session_id: str | None = None,
    max_suppression_seconds: float = 1800.0,
    inspector_callback: InspectorCallback | None = None,
    timeout_scope_ref: list[anyio.CancelScope | None] | None = None,
    has_pending_tasks: Callable[[], bool] | None = None,
) -> None:
    """Kill the child if stdout stops growing for idle_output_timeout seconds."""
    import time as _time

    last_size: int = 0
    last_growth_time: float = _time.monotonic()
    suppression_start_marker: float | None = None
    while True:
        await anyio.sleep(_poll_interval)
        if trigger.is_set():
            return
        try:
            current_size = stdout_path.stat().st_size
        except OSError:
            last_size = 0
            continue
        if current_size > last_size:
            last_size = current_size
            last_growth_time = _time.monotonic()
            suppression_start_marker = None
        elif _time.monotonic() - last_growth_time >= idle_output_timeout:
            authoritative_task_active = has_pending_tasks is not None and has_pending_tasks()
            marker_active = marker_dir is not None and _has_active_execution_marker(
                marker_dir, session_id=session_id
            )
            if authoritative_task_active or marker_active:
                now = _time.monotonic()
                if suppression_start_marker is None:
                    suppression_start_marker = now
                    logger.debug(
                        "stdout_idle_stall_suppression_evaluated",
                        marker_dir_present=True,
                        session_id=session_id,
                    )
                elapsed = now - suppression_start_marker
                if elapsed < max_suppression_seconds:
                    logger.warning(
                        "stdout_idle_stall_suppressed",
                        marker_dir=str(marker_dir),
                        session_id=session_id,
                        suppression_elapsed=elapsed,
                        max_suppression_seconds=max_suppression_seconds,
                    )
                    continue
            logger.debug(
                "stdout_idle_stall_suppression_evaluated",
                marker_dir_present=marker_dir is not None,
                session_id=session_id,
                **(
                    {"suppression_skipped_reason": "marker_dir_none"} if marker_dir is None else {}
                ),
            )
            logger.warning(
                "stdout idle for %ss — firing IDLE_STALL",
                idle_output_timeout,
            )
            spared_at = await _inspect_stdout_idle(
                stdout_path,
                last_growth_time,
                acc,
                inspector_callback,
                timeout_scope_ref,
                marker_dir=marker_dir,
                session_id=session_id,
            )
            if spared_at is not None:
                last_growth_time = spared_at
                continue

            acc.idle_stall = True
            trigger.set()
            return


async def _inspect_stdout_idle(
    stdout_path: Path,
    last_growth_time: float,
    acc: RaceAccumulator,
    inspector_callback: InspectorCallback | None,
    timeout_scope_ref: list[anyio.CancelScope | None] | None,
    *,
    marker_dir: Path | None,
    session_id: str | None,
) -> float | None:
    """Run the inspector and return the idle-clock reset time when it spares the process."""
    import time as _time

    if inspector_callback is None:
        return None
    invoke = True
    budget = INSPECTOR_MAX_SECONDS
    scope = timeout_scope_ref[0] if timeout_scope_ref else None
    if scope is not None:
        remaining = scope.deadline - _time.monotonic()
        budget = min(INSPECTOR_MAX_SECONDS, remaining - CLEANUP_BUDGET_SECONDS)
        if budget <= 0:
            logger.debug("inspector_skipped_insufficient_time", remaining=remaining)
            invoke = False
    elif timeout_scope_ref is not None:
        logger.debug("inspector_skipped_scope_not_ready")
        invoke = False
    if not invoke:
        return None
    marker_present = marker_dir is not None and _has_active_execution_marker(
        marker_dir, session_id=session_id
    )
    evidence = _package_evidence(
        stdout_path,
        idle_seconds=_time.monotonic() - last_growth_time,
        execution_marker_present=marker_present,
    )
    try:
        with anyio.fail_after(budget):
            verdict = await inspector_callback(evidence)
    except TimeoutError:
        logger.warning("inspector_callback_timed_out", budget=budget)
        return None
    if verdict is not None and verdict.action == "SPARE":
        spared_at = _time.monotonic()
        logger.info(
            "inspector_spare",
            reasoning=verdict.reasoning,
            confidence=verdict.confidence,
            elapsed=verdict.elapsed_seconds,
        )
        return spared_at
    if verdict is not None:
        acc.inspector_verdict = verdict
    return None


async def _watch_child_activity(
    pid: int,
    timeout_scope_ref: list[anyio.CancelScope | None],
    max_extension_seconds: float,
    trigger: anyio.Event,
    _poll_interval: float = 30.0,
    *,
    marker_dir: Path | None = None,
    session_id: str | None = None,
    has_pending_tasks: Callable[[], bool] | None = None,
) -> None:
    """Extend the wall-clock deadline while managed child activity remains active."""
    _first_observed_deadline: float | None = None

    while not trigger.is_set():
        await anyio.sleep(_poll_interval)
        if trigger.is_set():
            return

        scope = timeout_scope_ref[0]
        if scope is None:
            continue

        if _first_observed_deadline is None:
            _first_observed_deadline = scope.deadline

        active = (
            (has_pending_tasks is not None and has_pending_tasks())
            or _has_active_child_processes(pid)
            or _has_active_api_connection(pid)
            or (
                marker_dir is not None
                and _has_active_execution_marker(marker_dir, session_id=session_id)
            )
        )
        if not active:
            continue

        cap = _first_observed_deadline + max_extension_seconds
        desired = anyio.current_time() + _poll_interval * 2
        new_deadline = min(desired, cap)
        if new_deadline > scope.deadline:
            logger.debug(
                "deadline_extended",
                extension=new_deadline - scope.deadline,
                new_deadline=new_deadline,
                cap=cap,
            )
            scope.deadline = new_deadline
        if trigger.is_set():
            return


async def _watch_completion_eligibility(
    acc: RaceAccumulator,
    trigger: anyio.Event,
    channel_b_selected: anyio.Event,
    completion_drain_timeout: float,
    child_deferral_ceiling: float,
    stream_parser: StreamParser,
    session_log_enabled: bool,
    _poll_interval: float = 0.05,
) -> None:
    """Release a completion candidate only after both owned streams are caught up."""
    await acc.completion_candidate_event.wait()
    if session_log_enabled and acc.channel_b_cursor is None:
        with anyio.move_on_after(completion_drain_timeout):
            await channel_b_selected.wait()
    started = min(
        timestamp
        for timestamp in (acc.channel_a_candidate_at, acc.channel_b_candidate_at)
        if timestamp is not None
    )
    while not trigger.is_set():
        if acc.stdout_cursor is not None:
            fold_event_cursor(acc.stdout_cursor, stream_parser, acc.observe_event)
        if acc.channel_b_cursor is not None:
            fold_event_cursor(acc.channel_b_cursor, stream_parser, acc.observe_event)
        if not acc.has_unresolved_obligations():
            acc.channel_a_confirmed = acc.channel_a_candidate_at is not None
            trigger.set()
            return
        if anyio.current_time() - started >= child_deferral_ceiling:
            acc.completion_ceiling_expired = True
            acc.channel_a_confirmed = acc.channel_a_candidate_at is not None
            trigger.set()
            return
        await anyio.sleep(_poll_interval)


def _enroll_race_watchers(
    tg: anyio.abc.TaskGroup,
    *,
    _watch_process: Callable[..., Any],
    _watch_heartbeat: Callable[..., Any],
    _extract_stdout_session_id: Callable[..., Any],
    _watch_session_log: Callable[..., Any],
    _watch_completion_eligibility: Callable[..., Any],
    _watch_stdout_idle: Callable[..., Any],
    owner: OwnedProcessGroup,
    line_driver_session: LineDriverSession,
    process: subprocess.Popen[Any],
    capture_file: IO[bytes],
    acc: RaceAccumulator,
    trigger: anyio.Event,
    stdout_path: Path,
    completion_record_types: frozenset[str],
    completion_marker: str,
    stream_parser: StreamParser | None,
    heartbeat_poll: float,
    session_log_dir: Path | None,
    stale_threshold: float,
    spawn_time: float,
    session_record_types: frozenset[str],
    observed_pid: int,
    channel_b_ready: anyio.Event,
    phase1_poll: float,
    phase2_poll: float,
    phase1_timeout: float,
    session_id_timeout: float,
    stdout_session_id_ready: anyio.Event,
    max_suppression_seconds: float | None,
    marker_dir: Path | None,
    session_id: str | None,
    on_session_id_resolved: Callable[[str], None] | None,
    backend_resume_session_id: str,
    channel_b_selected: anyio.Event,
    lifecycle_observation_enabled: bool,
    lifecycle_parser: StreamParser,
    completion_drain_timeout: float,
    child_deferral_ceiling: float,
    idle_output_timeout: float | None,
    inspector_callback: InspectorCallback | None,
    timeout_scope_ref: list[anyio.CancelScope | None],
) -> None:
    """Enroll the pre-tracing watchers through facade-resolved patch seams."""
    tg.start_soon(_watch_process, owner, acc, trigger)
    line_driver_session.start(tg, process, capture_file=capture_file, trigger=trigger)
    tg.start_soon(
        functools.partial(
            _watch_heartbeat,
            stream_parser=stream_parser,
            _poll_interval=heartbeat_poll,
        ),
        stdout_path,
        completion_record_types,
        completion_marker,
        acc,
        trigger,
    )
    if session_log_dir is not None:
        tg.start_soon(
            functools.partial(
                _extract_stdout_session_id,
                stream_parser=stream_parser,
                on_session_id_resolved=on_session_id_resolved,
            ),
            stdout_path,
            acc,
            stdout_session_id_ready,
        )
        tg.start_soon(
            _watch_session_log,
            session_log_dir,
            completion_marker,
            stale_threshold,
            spawn_time,
            session_record_types,
            observed_pid,
            acc,
            trigger,
            channel_b_ready,
            phase1_poll,
            phase2_poll,
            phase1_timeout,
            session_id_timeout,
            stdout_session_id_ready,
            max_suppression_seconds,
            marker_dir,
            session_id,
            on_session_id_resolved,
            backend_resume_session_id,
            channel_b_selected,
        )
    if lifecycle_observation_enabled:
        tg.start_soon(
            _watch_completion_eligibility,
            acc,
            trigger,
            channel_b_selected,
            completion_drain_timeout,
            child_deferral_ceiling,
            lifecycle_parser,
            session_log_dir is not None,
        )
    if idle_output_timeout is not None and idle_output_timeout > 0:
        tg.start_soon(
            functools.partial(
                _watch_stdout_idle,
                stdout_path,
                idle_output_timeout,
                acc,
                trigger,
                marker_dir=marker_dir,
                session_id=session_id,
                max_suppression_seconds=max_suppression_seconds or 1800.0,
                inspector_callback=inspector_callback,
                timeout_scope_ref=timeout_scope_ref,
                has_pending_tasks=(
                    acc.has_unresolved_obligations if lifecycle_observation_enabled else None
                ),
            ),
        )


def _enroll_child_activity_watcher(
    tg: anyio.abc.TaskGroup,
    *,
    _watch_child_activity: Callable[..., Any],
    enable_deadline_extension: bool,
    observed_pid: int | None,
    timeout_scope_ref: list[anyio.CancelScope | None],
    max_extension_seconds: float,
    trigger: anyio.Event,
    marker_dir: Path | None,
    session_id: str | None,
    lifecycle_observation_enabled: bool,
    acc: RaceAccumulator,
) -> None:
    if enable_deadline_extension and observed_pid is not None:
        tg.start_soon(
            functools.partial(
                _watch_child_activity,
                observed_pid,
                timeout_scope_ref,
                max_extension_seconds,
                trigger,
                marker_dir=marker_dir,
                session_id=session_id,
                has_pending_tasks=(
                    acc.has_unresolved_obligations if lifecycle_observation_enabled else None
                ),
            ),
        )


async def _await_race_and_drain(
    tg: anyio.abc.TaskGroup,
    *,
    _watch_child_activity: Callable[..., Any],
    timeout: float,
    trigger: anyio.Event,
    timeout_scope_ref: list[anyio.CancelScope | None],
    enable_deadline_extension: bool,
    observed_pid: int | None,
    max_extension_seconds: float,
    marker_dir: Path | None,
    session_id: str | None,
    lifecycle_observation_enabled: bool,
    acc: RaceAccumulator,
    session_log_dir: Path | None,
    channel_b_ready: anyio.Event,
    completion_drain_timeout: float,
    proc_log: SupportsDebug,
) -> anyio.CancelScope | None:
    """Await the race, draining Channel B after exit, then cancel its watchers."""
    _enroll_child_activity_watcher(
        tg,
        _watch_child_activity=_watch_child_activity,
        enable_deadline_extension=enable_deadline_extension,
        observed_pid=observed_pid,
        timeout_scope_ref=timeout_scope_ref,
        max_extension_seconds=max_extension_seconds,
        trigger=trigger,
        marker_dir=marker_dir,
        session_id=session_id,
        lifecycle_observation_enabled=lifecycle_observation_enabled,
        acc=acc,
    )
    with anyio.move_on_after(timeout) as timeout_scope:
        timeout_scope_ref[0] = timeout_scope
        await trigger.wait()
    if acc.process_exited and acc.channel_b_status is None and session_log_dir is not None:
        proc_log.debug(
            "symmetric_drain_started",
            reason="process_exited_before_channel_b",
            drain_timeout=completion_drain_timeout,
        )
        with anyio.move_on_after(completion_drain_timeout):
            await channel_b_ready.wait()
        proc_log.debug(
            "symmetric_drain_complete",
            channel_b_status=acc.channel_b_status,
            channel_b_deposited=acc.channel_b_status is not None,
        )
    tg.cancel_scope.cancel()
    return timeout_scope_ref[0]
