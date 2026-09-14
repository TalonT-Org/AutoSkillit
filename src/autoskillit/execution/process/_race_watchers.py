"""Watcher enrollment and completion race coordination for managed processes."""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import anyio
import anyio.abc

from autoskillit.execution.process._process_race import RaceAccumulator

if TYPE_CHECKING:
    from autoskillit.core import InspectorCallback, StreamParser
    from autoskillit.execution.process._lifecycle.line_driver_tee import LineDriverSession
    from autoskillit.execution.process._process_kill import OwnedProcessGroup


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
    process: Any,
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
    proc_log: Any,
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
