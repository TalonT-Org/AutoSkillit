"""Subprocess lifecycle utilities providing pipe-blocking immunity."""

from __future__ import annotations

import functools
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, assert_never

import anyio
import anyio.abc

from autoskillit.core import (
    DEFAULT_CLEANUP_BUDGET_SECONDS,
    ChannelConfirmation,
    CleanupOutcome,
    KillReason,
    LifecycleDecision,
    StreamParserFactory,
    SubprocessResult,
    TerminationAction,
    TerminationReason,
    get_logger,
    read_starttime_ticks,
)
from autoskillit.execution.process._channel_a_pump import (
    ChannelAPumpState,
    bind_parser,
    run_channel_a_pump,
)
from autoskillit.execution.process._lifecycle_actor import (
    REQUEST_CAPACITY,
    ActorIngressEndpoint,
    _LifecycleActorState,
    make_actor_ingress,
    run_lifecycle_actor,
)
from autoskillit.execution.process._lifecycle_actor import (
    watch_process_with_lifecycle as _watch_process_with_lifecycle,
)
from autoskillit.execution.process._lifecycle_actor import (
    watch_session_log_with_lifecycle as _watch_session_log_with_lifecycle,
)
from autoskillit.execution.process._process_io import create_temp_io, read_temp_output
from autoskillit.execution.process._process_jsonl import (
    _jsonl_contains_marker,
    _jsonl_has_record_type,
    _jsonl_last_record_type,
    _marker_is_standalone,
)
from autoskillit.execution.process._process_kill import (
    _finalize_owned_process_sync,
    _OwnedProcessFinalizer,
    _TerminationExecution,
    _TerminationSignalState,
    async_kill_process_tree,
    kill_process_tree,
)
from autoskillit.execution.process._process_monitor import (
    ProcessActivityTracker,
    SessionMonitorResult,
    _has_active_api_connection,
    _has_active_execution_marker,
    _heartbeat,
    _session_log_monitor,
)
from autoskillit.execution.process._process_ownership import make_tracker
from autoskillit.execution.process._process_pty import pty_wrap_command
from autoskillit.execution.process._process_race import (
    RaceAccumulator,
    RaceSignals,
    _apply_lifecycle_completion_authority,
    _apply_session_monitor_result,
    _extract_stdout_session_id,
    _watch_child_activity,
    _watch_heartbeat,
    _watch_process,
    _watch_session_log,
    _watch_stdout_idle,
    resolve_termination,
)

if TYPE_CHECKING:
    import structlog

    from autoskillit.config import LinuxTracingConfig
    from autoskillit.core import InspectorCallback
    from autoskillit.execution.linux_tracing import TraceTarget

logger = get_logger(__name__)


__all__ = [
    "DefaultSubprocessRunner",
    "ProcessActivityTracker",
    "_extract_stdout_session_id",
    "_resolve_session_id",
    "RaceAccumulator",
    "RaceSignals",
    "_has_active_api_connection",
    "_has_active_execution_marker",
    "_heartbeat",
    "_jsonl_contains_marker",
    "_jsonl_has_record_type",
    "_jsonl_last_record_type",
    "_marker_is_standalone",
    "_session_log_monitor",
    "_watch_heartbeat",
    "_watch_process",
    "_watch_session_log",
    "async_kill_process_tree",
    "create_temp_io",
    "decide_termination_action",
    "execute_termination_action",
    "kill_process_tree",
    "pty_wrap_command",
    "read_temp_output",
    "resolve_termination",
    "run_managed_async",
    "run_managed_sync",
]


def _resolve_session_id(
    stdout_session_id: str | None,
    channel_b_session_id: str,
) -> str:
    """Merge session ID sources: stdout type=system wins; Channel B JSONL filename fallback."""
    return stdout_session_id or channel_b_session_id or ""


def decide_termination_action(
    termination: TerminationReason,
    *,
    timeout_fired: bool,
    process_exited: bool,
    owned_processes: bool,
    lifecycle_decision: LifecycleDecision,
) -> TerminationAction:
    """Map race, lifecycle, and ownership state to one termination action.

    ``NO_KILL`` requires an exited root and a clear ownership preflight.
    """
    if timeout_fired:
        return TerminationAction.IMMEDIATE_KILL
    if lifecycle_decision in {
        LifecycleDecision.CHILD_WORK_FAILED,
        LifecycleDecision.CATCH_UP_FAILED,
    }:
        return TerminationAction.IMMEDIATE_KILL
    if termination in {
        TerminationReason.IDLE_STALL,
        TerminationReason.STALE,
        TerminationReason.TIMED_OUT,
        TerminationReason.HEALTH_INSPECTOR,
    }:
        return TerminationAction.IMMEDIATE_KILL
    if process_exited and not owned_processes:
        return TerminationAction.NO_KILL
    match termination:
        case TerminationReason.NATURAL_EXIT | TerminationReason.SIGNAL_DEATH:
            return TerminationAction.IMMEDIATE_KILL
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
    termination: TerminationReason,
    proc: anyio.abc.Process,
    process_exited_event: anyio.Event,
    grace_seconds: float,
    proc_log: structlog.BoundLogger,
    finalizer: _OwnedProcessFinalizer,
    retained_ownership_at_exit: bool = False,
) -> _TerminationExecution:
    """Execute one decision through the invocation-owned finalizer."""
    finalizer.start_deadline()
    match action:
        case TerminationAction.NO_KILL:
            cleanup = await finalizer.run()
            return _natural_cleanup_execution(
                termination, finalizer, cleanup, retained_ownership_at_exit
            )
        case TerminationAction.DRAIN_THEN_KILL_IF_ALIVE:
            grace_budget = finalizer.remaining_time(limit=grace_seconds)
            if proc.returncode is None and grace_budget > 0:
                with anyio.move_on_after(grace_budget):
                    await process_exited_event.wait()
            if proc.returncode is not None:
                proc_log.debug("natural_exit_after_drain", returncode=proc.returncode)
            else:
                proc_log.debug("grace_expired_killing", grace_seconds=grace_budget)
            cleanup = await finalizer.run()
            return _natural_cleanup_execution(
                termination, finalizer, cleanup, retained_ownership_at_exit
            )
        case TerminationAction.IMMEDIATE_KILL:
            cleanup = await finalizer.run()
            return _TerminationExecution(KillReason.INFRA_KILL, cleanup)
        case _ as unreachable:
            assert_never(unreachable)


def _natural_cleanup_execution(
    termination: TerminationReason,
    finalizer: _OwnedProcessFinalizer,
    cleanup: CleanupOutcome,
    retained_ownership_at_exit: bool,
) -> _TerminationExecution:
    if retained_ownership_at_exit and termination is TerminationReason.COMPLETED:
        reason = KillReason.KILL_AFTER_COMPLETION
    elif cleanup.succeeded and not finalizer.signaled:
        reason = KillReason.NATURAL_EXIT
    elif termination is TerminationReason.COMPLETED:
        reason = KillReason.KILL_AFTER_COMPLETION
    else:
        reason = KillReason.INFRA_KILL
    return _TerminationExecution(reason, cleanup)


def _stdout_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


async def run_managed_async(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
    input_data: str | None = None,
    env: Mapping[str, str] | None = None,
    pty_mode: bool = False,
    completion_record_types: frozenset[str] = frozenset({"result"}),
    session_log_dir: Path | None = None,
    completion_marker: str = "",
    stale_threshold: float = 1200,
    session_record_types: frozenset[str] = frozenset({"assistant"}),
    completion_drain_timeout: float = 5.0,
    natural_exit_grace_seconds: float = 3.0,
    linux_tracing_config: LinuxTracingConfig | None = None,
    idle_output_timeout: float | None = None,
    max_suppression_seconds: float | None = None,
    on_pid_resolved: Callable[[int, int], None] | None = None,
    enable_deadline_extension: bool = False,
    max_extension_seconds: float = 7200,
    _phase1_poll: float = 1.0,
    _phase2_poll: float = 2.0,
    _heartbeat_poll: float = 0.5,
    _phase1_timeout: float = 30.0,
    _session_id_timeout: float = 1.0,
    marker_dir: Path | None = None,
    marker_scope_session_id: str | None = None,
    stream_parser_factory: StreamParserFactory | None = None,
    parent_candidate_normalizer: Callable[[dict[str, Any], int], Any] | None = None,
    inspector_callback: InspectorCallback | None = None,
    workload_basenames: frozenset[str] | None = None,
    on_session_id_resolved: Callable[[str], None] | None = None,
    cleanup_budget_seconds: float = DEFAULT_CLEANUP_BUDGET_SECONDS,
) -> SubprocessResult:
    """Run one async subprocess through lifecycle monitoring and owned cleanup."""
    lifecycle_enabled = bool(completion_marker)
    if lifecycle_enabled:
        if not callable(stream_parser_factory):
            raise TypeError("stream_parser_factory must be callable for a nonempty marker")
        if not callable(parent_candidate_normalizer):
            raise TypeError("parent_candidate_normalizer must be callable for a nonempty marker")

    # Capture workload basename before PTY wrapping rewrites cmd (#806)
    _workload_basename = Path(cmd[0]).name if cmd else ""

    if pty_mode:
        cmd = pty_wrap_command(cmd)

    with create_temp_io(input_data) as (stdout_file, stderr_file, stdin_path):
        stdout_path = Path(stdout_file.name)
        stderr_path = Path(stderr_file.name)

        # Build stdin handle — use DEVNULL when no input to prevent
        # inheriting parent's stdin (e.g. MCP server socket that never closes)
        stdin_handle = None
        if stdin_path is not None:
            stdin_handle = open(stdin_path)  # noqa: SIM115

        try:
            # uvloop's Cython layer requires type(env) is dict — coerce at
            # the external API boundary to preserve MappingProxyType internally.
            _env: dict[str, str] | None = dict(env) if env is not None else None
            # Capture spawn_time before launching the subprocess so that the
            # session JSONL file's ctime is always > spawn_time even when task
            # group setup takes longer than the subprocess's initial sleep.
            # Capturing it after open_process() creates a race: under CI load
            # the subprocess writes its JSONL before time.time() is evaluated,
            # causing st_ctime < spawn_time and Phase 1 to never find the file.
            _spawn_time = time.time()
            proc = await anyio.open_process(
                cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                cwd=cwd,
                env=_env,
                start_new_session=True,
            )
            _finalizer = _OwnedProcessFinalizer(
                tracker=make_tracker(),
                budget_seconds=cleanup_budget_seconds,
                process=proc,
                owned_root_pid=proc.pid,
            )
            _ownership_tracker = _finalizer.tracker
            _ownership_tracker.enrich_root_identity()
            if lifecycle_enabled:
                assert stream_parser_factory is not None
                lifecycle_parser = stream_parser_factory()
            else:
                lifecycle_parser = None
            _activity_tracker = ProcessActivityTracker()

            # Resolve the workload TraceTarget — the PID that should be observed.
            # anyio.open_process returns the spawn PID, which in PTY mode is the
            # script(1) wrapper, not claude. resolve_trace_target walks descendants
            # to find the actual workload by basename. Raising here (on miss) is
            # intentional: a silent fallback to proc.pid recreates issue #806.
            _target: TraceTarget | None = None
            _observed_pid: int = proc.pid
            _tracked_comm: str | None = None
            if linux_tracing_config is not None:
                from autoskillit.execution.linux_tracing import (
                    LINUX_TRACING_AVAILABLE,
                    resolve_trace_target,
                    trace_target_from_pid,
                )

                if pty_mode and LINUX_TRACING_AVAILABLE:
                    # PTY mode: proc.pid is the script(1) wrapper — resolve to workload
                    _target = await resolve_trace_target(
                        root_pid=proc.pid,
                        expected_basename=_workload_basename,
                        timeout=2.0,
                        expected_basenames=workload_basenames,
                    )
                else:
                    # Non-PTY mode: proc.pid IS the workload (direct child)
                    _target = trace_target_from_pid(proc.pid)
                assert _target is not None
                _observed_pid = _target.pid
                _tracked_comm = _target.comm

            if on_pid_resolved is not None and _observed_pid > 0:
                _ticks: int = 0
                if _target is not None and hasattr(_target, "starttime_ticks"):
                    _ticks = _target.starttime_ticks or 0
                if _ticks == 0:
                    _ticks = read_starttime_ticks(_observed_pid) or 0
                if _ticks == 0:
                    logger.warning(
                        "starttime_ticks_zero",
                        pid=_observed_pid,
                        msg="Process identity verification degraded — starttime_ticks=0",
                    )
                on_pid_resolved(_observed_pid, _ticks)

            termination = TerminationReason.NATURAL_EXIT
            _channel_confirmation = ChannelConfirmation.UNMONITORED

            proc_log = logger.bind(pid=_observed_pid, comm=_tracked_comm)
            proc_log.debug(
                "run_managed_async_entry",
                cmd_summary=cmd[0] if cmd else "<empty>",
                cwd=str(cwd),
                timeout=timeout,
                stale_threshold=stale_threshold,
                session_log_dir=str(session_log_dir) if session_log_dir else None,
                session_monitor_enabled=session_log_dir is not None,
            )

            acc = RaceAccumulator()
            trigger = anyio.Event()
            channel_b_ready = anyio.Event()
            stdout_session_id_ready = anyio.Event()
            timeout_scope_ref: list[anyio.CancelScope | None] = [None]

            def _record_stdout_session_id(resolved_session_id: str) -> None:
                if acc.stdout_session_id:
                    return
                acc.stdout_session_id = resolved_session_id
                stdout_session_id_ready.set()
                if on_session_id_resolved is not None:
                    on_session_id_resolved(resolved_session_id)

            def _record_lifecycle_state(state: _LifecycleActorState) -> None:
                _apply_session_monitor_result(
                    acc,
                    SessionMonitorResult(
                        status=acc.channel_b_status,
                        session_id=acc.channel_b_session_id,
                        orphaned_tool_result=acc.channel_b_orphaned_tool_result,
                        snapshot=state.snapshot,
                        decision=state.decision,
                        eligible_candidate=state.eligible_candidate,
                        eligible_source=state.eligible_source,
                        sightings=state.sightings,
                    ),
                )
                if state.decision is not LifecycleDecision.CONTINUE:
                    trigger.set()

            def _record_monitor_result(result: SessionMonitorResult) -> None:
                _apply_session_monitor_result(acc, result)

            pump_state: ChannelAPumpState | None = None
            if lifecycle_enabled:
                assert lifecycle_parser is not None
                pump_state = ChannelAPumpState(
                    completion_marker=completion_marker,
                    stdout_path=stdout_path,
                    on_session_id_resolved=_record_stdout_session_id,
                )
                bind_parser(pump_state, lifecycle_parser)

            tracing_handle = None
            timeout_scope: anyio.CancelScope | None
            if lifecycle_enabled and pump_state is not None:
                ingress = make_actor_ingress(REQUEST_CAPACITY)
                request_semaphore = anyio.Semaphore(REQUEST_CAPACITY)
                pump_command_send, pump_command_receive = anyio.create_memory_object_stream(
                    REQUEST_CAPACITY
                )
                pump_remove_send, pump_remove_receive = anyio.create_memory_object_stream(
                    REQUEST_CAPACITY
                )
                producer_stop = anyio.Event()
                producers_done = anyio.Event()
                actor_done = anyio.Event()
                post_exit_scan = anyio.Event()
                producer_errors: list[BaseException] = []
                producer_count = 2 + int(session_log_dir is not None)
                completed_producers = 0
                actor_emergency_scope: list[anyio.CancelScope | None] = [None]

                async def _supervise(
                    producer: Callable[[], Any], endpoint: ActorIngressEndpoint
                ) -> None:
                    nonlocal completed_producers
                    try:
                        await producer()
                    except anyio.get_cancelled_exc_class():
                        if not producer_stop.is_set():
                            raise
                    except BaseException as exc:  # noqa: BLE001
                        logger.warning(
                            "lifecycle_producer_failed",
                            exc_info=True,
                            producer=endpoint.producer,
                        )
                        producer_errors.append(exc)
                        trigger.set()
                    finally:
                        with anyio.CancelScope(shield=True):
                            await endpoint.aclose()
                        completed_producers += 1
                        if completed_producers == producer_count:
                            producers_done.set()

                async def _actor_runner() -> None:
                    with anyio.CancelScope(shield=True) as actor_scope:
                        actor_emergency_scope[0] = actor_scope
                        await run_lifecycle_actor(
                            ingress,
                            pump_command_send,
                            _record_lifecycle_state,
                            actor_done,
                            pump_remove_send,
                        )

                if session_log_dir is None:
                    await ingress.channel_b.aclose()
                async with anyio.create_task_group() as actor_tg:
                    actor_tg.start_soon(_actor_runner)
                    async with anyio.create_task_group() as producer_tg:
                        producer_tg.start_soon(
                            _supervise,
                            functools.partial(
                                run_channel_a_pump,
                                pump_state,
                                pump_command_receive,
                                producer_stop,
                                ingress.channel_a,
                                remove_receive=pump_remove_receive,
                                poll_interval=_heartbeat_poll,
                            ),
                            ingress.channel_a,
                        )
                        producer_tg.start_soon(
                            _supervise,
                            functools.partial(
                                _watch_process_with_lifecycle,
                                proc,
                                acc,
                                _ownership_tracker,
                                ingress.process_exit,
                                request_semaphore,
                                stdout_path,
                                post_exit_scan,
                                session_log_dir is not None,
                                producer_stop,
                                trigger,
                                completion_drain_timeout,
                                _record_monitor_result,
                            ),
                            ingress.process_exit,
                        )
                        if session_log_dir is not None:
                            assert parent_candidate_normalizer is not None
                            producer_tg.start_soon(
                                _supervise,
                                functools.partial(
                                    _watch_session_log_with_lifecycle,
                                    session_log_dir=session_log_dir,
                                    completion_marker=completion_marker,
                                    stale_threshold=stale_threshold,
                                    spawn_time=_spawn_time,
                                    session_record_types=session_record_types,
                                    pid=_observed_pid,
                                    activity_tracker=_activity_tracker,
                                    completion_drain_timeout=completion_drain_timeout,
                                    channel_b_ready=channel_b_ready,
                                    post_exit_scan=post_exit_scan,
                                    process_exited=acc.process_exited_event,
                                    phase1_poll=_phase1_poll,
                                    phase2_poll=_phase2_poll,
                                    phase1_timeout=_phase1_timeout,
                                    session_id_timeout=_session_id_timeout,
                                    stdout_session_id_ready=stdout_session_id_ready,
                                    expected_session_id=lambda: acc.stdout_session_id,
                                    max_suppression_seconds=max_suppression_seconds or 1800.0,
                                    marker_dir=marker_dir,
                                    marker_scope_session_id=marker_scope_session_id,
                                    stdout_size=lambda: _stdout_size(stdout_path),
                                    endpoint=ingress.channel_b,
                                    semaphore=request_semaphore,
                                    producer_stop=producer_stop,
                                    parent_candidate_normalizer=parent_candidate_normalizer,
                                    on_result=_record_monitor_result,
                                    trigger=trigger,
                                ),
                                ingress.channel_b,
                            )
                        if idle_output_timeout is not None and idle_output_timeout > 0:
                            producer_tg.start_soon(
                                functools.partial(
                                    _watch_stdout_idle,
                                    stdout_path,
                                    idle_output_timeout,
                                    acc,
                                    trigger,
                                    marker_dir=marker_dir,
                                    marker_scope_session_id=marker_scope_session_id,
                                    max_suppression_seconds=max_suppression_seconds or 1800.0,
                                    inspector_callback=inspector_callback,
                                    timeout_scope_ref=timeout_scope_ref,
                                )
                            )
                        if linux_tracing_config is not None and _target is not None:
                            from autoskillit.execution.linux_tracing import start_linux_tracing

                            tracing_handle = start_linux_tracing(
                                target=_target,
                                config=linux_tracing_config,
                                tg=producer_tg,
                            )
                        if enable_deadline_extension and _observed_pid is not None:
                            producer_tg.start_soon(
                                functools.partial(
                                    _watch_child_activity,
                                    _observed_pid,
                                    timeout_scope_ref,
                                    max_extension_seconds,
                                    trigger,
                                    marker_dir=marker_dir,
                                    marker_scope_session_id=marker_scope_session_id,
                                    activity_tracker=_activity_tracker,
                                )
                            )
                        with anyio.move_on_after(timeout) as _ts:
                            timeout_scope_ref[0] = _ts
                            await trigger.wait()
                        timeout_scope = timeout_scope_ref[0]
                        producer_stop.set()
                        with anyio.move_on_after(completion_drain_timeout):
                            await producers_done.wait()
                        producer_tg.cancel_scope.cancel()
                    with anyio.CancelScope(shield=True):
                        with anyio.move_on_after(completion_drain_timeout):
                            await actor_done.wait()
                        if not actor_done.is_set():
                            await ingress.aclose_receivers()
                            if actor_emergency_scope[0] is not None:
                                actor_emergency_scope[0].cancel()
                if not actor_done.is_set():
                    raise RuntimeError("lifecycle_actor_drain_incomplete")
                if producer_errors:
                    raise producer_errors[0]
            else:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(
                        _watch_process,
                        proc,
                        acc,
                        trigger,
                        _ownership_tracker,
                    )
                    tg.start_soon(
                        functools.partial(
                            _watch_heartbeat,
                            _poll_interval=_heartbeat_poll,
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
                            _spawn_time,
                            session_record_types,
                            _observed_pid,
                            completion_drain_timeout,
                            acc,
                            trigger,
                            channel_b_ready,
                            _phase1_poll,
                            _phase2_poll,
                            _phase1_timeout,
                            _session_id_timeout,
                            stdout_session_id_ready,
                            max_suppression_seconds,
                            marker_dir,
                            marker_scope_session_id,
                            _activity_tracker,
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
                                marker_scope_session_id=marker_scope_session_id,
                                max_suppression_seconds=max_suppression_seconds or 1800.0,
                                inspector_callback=inspector_callback,
                                timeout_scope_ref=timeout_scope_ref,
                            ),
                        )
                    if linux_tracing_config is not None and _target is not None:
                        from autoskillit.execution.linux_tracing import start_linux_tracing

                        tracing_handle = start_linux_tracing(
                            target=_target,
                            config=linux_tracing_config,
                            tg=tg,
                        )
                    if enable_deadline_extension and _observed_pid is not None:
                        tg.start_soon(
                            functools.partial(
                                _watch_child_activity,
                                _observed_pid,
                                timeout_scope_ref,
                                max_extension_seconds,
                                trigger,
                                marker_dir=marker_dir,
                                marker_scope_session_id=marker_scope_session_id,
                                activity_tracker=_activity_tracker,
                            ),
                        )
                    with anyio.move_on_after(timeout) as _ts:
                        timeout_scope_ref[0] = _ts
                        await trigger.wait()
                    timeout_scope = timeout_scope_ref[0]
                    if acc.process_exited and acc.channel_b_status is None and session_log_dir:
                        with anyio.move_on_after(completion_drain_timeout):
                            await channel_b_ready.wait()
                    tg.cancel_scope.cancel()

            signals = acc.to_race_signals()
            termination, _channel_confirmation = resolve_termination(signals)
            if lifecycle_enabled:
                termination, _channel_confirmation = _apply_lifecycle_completion_authority(
                    termination,
                    _channel_confirmation,
                    signals,
                )

            snapshots_data: list[dict[str, object]] | None = None
            if tracing_handle is not None:
                accumulated = tracing_handle.stop()
                snapshots_data = [asdict(s) for s in accumulated]
                if signals.exit_snapshot is not None:
                    snapshots_data.append(signals.exit_snapshot)
            elif signals.exit_snapshot is not None:
                snapshots_data = [signals.exit_snapshot]

            if timeout_scope is not None and timeout_scope.cancelled_caught:
                termination = TerminationReason.TIMED_OUT
            ownership_preflight = await _finalizer.preflight()
            action = decide_termination_action(
                termination,
                timeout_fired=timeout_scope is not None and timeout_scope.cancelled_caught,
                process_exited=signals.process_exited,
                owned_processes=ownership_preflight.has_live_or_unknown,
                lifecycle_decision=signals.decision,
            )
            proc_log.debug(
                "kill_decision",
                termination=str(termination),
                action=str(action),
                reason=str(action),
                process_exited=signals.process_exited,
                lifecycle_decision=signals.decision,
                eligible_source=signals.eligible_source,
                channel_b=signals.channel_b_status,
            )
            termination_execution = await execute_termination_action(
                action,
                proc=proc,
                process_exited_event=signals.process_exited_event,
                grace_seconds=natural_exit_grace_seconds,
                proc_log=proc_log,
                finalizer=_finalizer,
                termination=termination,
                retained_ownership_at_exit=(
                    signals.process_exited and ownership_preflight.has_live_or_unknown
                ),
            )

            # Flush and close before reading
            stdout_file.close()
            stderr_file.close()

            stdout, stderr = read_temp_output(stdout_path, stderr_path)

            sub_result = SubprocessResult(
                returncode=proc.returncode if proc.returncode is not None else -1,
                stdout=stdout,
                stderr=stderr,
                termination=termination,
                pid=_observed_pid,
                channel_confirmation=_channel_confirmation,
                proc_snapshots=snapshots_data,
                channel_b_session_id=signals.channel_b_session_id,
                session_id=_resolve_session_id(
                    signals.stdout_session_id, signals.channel_b_session_id
                ),
                kill_reason=termination_execution.kill_reason,
                tracked_comm=_tracked_comm,
                orphaned_tool_result=signals.channel_b_orphaned_tool_result,
                inspector_verdict=signals.inspector_verdict,
                cleanup_outcome=termination_execution.cleanup_outcome,
                lifecycle_snapshot=signals.snapshot,
                lifecycle_decision=signals.decision,
                lifecycle_candidate=signals.eligible_candidate,
                eligible_source=signals.eligible_source,
                sightings=signals.sightings,
            )
            proc_log.debug(
                "run_managed_async_result",
                returncode=sub_result.returncode,
                termination=str(sub_result.termination),
                channel=str(sub_result.channel_confirmation),
                stdout_len=len(sub_result.stdout),
                stderr_len=len(sub_result.stderr),
            )
            return sub_result
        except BaseException:
            with anyio.CancelScope(shield=True):
                if "tracing_handle" in locals() and tracing_handle is not None:
                    try:
                        tracing_handle.stop()
                    except BaseException as tracing_exc:
                        with suppress(BaseException):
                            logger.error(
                                "tracing_stop_failed",
                                error=tracing_exc,
                                exc_info=True,
                            )
                if "_finalizer" in locals():
                    try:
                        await _finalizer.run()
                    except BaseException as cleanup_exc:
                        with suppress(BaseException):
                            logger.error(
                                "exception_cleanup_failed",
                                error=cleanup_exc,
                                exc_info=True,
                            )
            raise
        finally:
            if stdin_handle is not None:
                try:
                    stdin_handle.close()
                except OSError:
                    pass


def run_managed_sync(
    cmd: list[str],
    *,
    cwd: Path | None,
    timeout: float,
    input_data: str | None = None,
    env: Mapping[str, str] | None = None,
) -> SubprocessResult:
    """Run one synchronous subprocess with timeout and owned cleanup."""
    with create_temp_io(input_data) as (stdout_file, stderr_file, stdin_path):
        stdout_path = Path(stdout_file.name)
        stderr_path = Path(stderr_file.name)

        # Build stdin handle — use DEVNULL when no input to prevent
        # inheriting parent's stdin (e.g. MCP server socket that never closes)
        stdin_handle = None
        if stdin_path is not None:
            stdin_handle = open(stdin_path)  # noqa: SIM115

        process: subprocess.Popen[bytes] | None = None
        ownership_tracker = make_tracker()
        cleanup_outcome = None
        signal_state = _TerminationSignalState()
        try:
            _env: dict[str, str] | None = dict(env) if env is not None else None
            process = subprocess.Popen(
                cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                cwd=cwd,
                env=_env,
                start_new_session=True,
            )
            ownership_tracker.seed_root(
                process.pid,
                process_group_id=process.pid,
                session_id=process.pid,
            )
            ownership_tracker.enrich_root_identity()

            termination = TerminationReason.NATURAL_EXIT
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                termination = TerminationReason.TIMED_OUT
                logger.warning(
                    "Process %d timed out after %ss, finalizing owned tree",
                    process.pid,
                    timeout,
                )

            cleanup_outcome = _finalize_owned_process_sync(
                process=process,
                tracker=ownership_tracker,
                budget_seconds=DEFAULT_CLEANUP_BUDGET_SECONDS,
                signal_state=signal_state,
            )

            # Flush and close before reading
            stdout_file.close()
            stderr_file.close()

            stdout, stderr = read_temp_output(stdout_path, stderr_path)

            return SubprocessResult(
                returncode=process.returncode if process.returncode is not None else -1,
                stdout=stdout,
                stderr=stderr,
                termination=termination,
                pid=process.pid,
                channel_confirmation=ChannelConfirmation.UNMONITORED,
                kill_reason=(
                    KillReason.INFRA_KILL
                    if termination is TerminationReason.TIMED_OUT
                    or signal_state.signaled
                    or not cleanup_outcome.succeeded
                    else KillReason.NATURAL_EXIT
                ),
                cleanup_outcome=cleanup_outcome,
            )
        except BaseException:
            if process is not None and cleanup_outcome is None:
                _finalize_owned_process_sync(
                    process=process,
                    tracker=ownership_tracker,
                    budget_seconds=DEFAULT_CLEANUP_BUDGET_SECONDS,
                )
            raise
        finally:
            if stdin_handle is not None:
                try:
                    stdin_handle.close()
                except OSError:
                    pass


class DefaultSubprocessRunner:
    """Implements SubprocessRunner protocol by delegating to run_managed_async."""

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: Mapping[str, str] | None = None,
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = False,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
        linux_tracing_config: LinuxTracingConfig | None = None,
        idle_output_timeout: float | None = None,
        max_suppression_seconds: float | None = None,
        on_pid_resolved: Callable[[int, int], None] | None = None,
        enable_deadline_extension: bool = False,
        max_extension_seconds: float = 7200,
        marker_dir: Path | None = None,
        marker_scope_session_id: str | None = None,
        stream_parser_factory: StreamParserFactory | None = None,
        parent_candidate_normalizer: Callable[[dict[str, Any], int], Any] | None = None,
        completion_record_types: frozenset[str] = frozenset({"result"}),
        session_record_types: frozenset[str] = frozenset({"assistant"}),
        inspector_callback: InspectorCallback | None = None,
        workload_basenames: frozenset[str] | None = None,
        on_session_id_resolved: Callable[[str], None] | None = None,
        cleanup_budget_seconds: float = DEFAULT_CLEANUP_BUDGET_SECONDS,
    ) -> SubprocessResult:
        return await run_managed_async(
            cmd,
            cwd=cwd,
            timeout=timeout,
            env=env,
            stale_threshold=stale_threshold,
            completion_marker=completion_marker,
            session_log_dir=session_log_dir,
            pty_mode=pty_mode,
            input_data=input_data,
            completion_drain_timeout=completion_drain_timeout,
            linux_tracing_config=linux_tracing_config,
            idle_output_timeout=idle_output_timeout,
            max_suppression_seconds=max_suppression_seconds,
            on_pid_resolved=on_pid_resolved,
            enable_deadline_extension=enable_deadline_extension,
            max_extension_seconds=max_extension_seconds,
            marker_dir=marker_dir,
            marker_scope_session_id=marker_scope_session_id,
            stream_parser_factory=stream_parser_factory,
            parent_candidate_normalizer=parent_candidate_normalizer,
            completion_record_types=completion_record_types,
            session_record_types=session_record_types,
            inspector_callback=inspector_callback,
            workload_basenames=workload_basenames,
            on_session_id_resolved=on_session_id_resolved,
            cleanup_budget_seconds=cleanup_budget_seconds,
        )
