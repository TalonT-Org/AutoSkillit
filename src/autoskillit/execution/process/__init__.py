"""Subprocess lifecycle utilities providing pipe-blocking immunity.

Shared building blocks for all subprocess-spawning code in the project.
Uses temp file I/O (not pipes) to eliminate FD-inheritance blocking, and
psutil-based process tree cleanup with SIGTERM→SIGKILL escalation.

Two composed functions wire the utilities together correctly:
- ``run_managed_async`` for async callers
- ``run_managed_sync`` for sync callers
"""

from __future__ import annotations

import functools
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

import anyio
import anyio.abc
import psutil
from anyio.streams.memory import MemoryObjectSendStream

from autoskillit.core import (
    DEFAULT_CLEANUP_BUDGET_SECONDS,
    ChannelBStatus,
    ChannelConfirmation,
    ChildLifecycleSnapshot,
    CompletionCandidate,
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
    CatchUpCancellationFact,
    ChannelBProposal,
    ProcessExitFact,
    run_lifecycle_actor,
)
from autoskillit.execution.process._process_io import create_temp_io, read_temp_output
from autoskillit.execution.process._process_jsonl import (
    _jsonl_contains_marker,
    _jsonl_has_record_type,
    _jsonl_last_record_type,
    _marker_is_standalone,
)
from autoskillit.execution.process._process_kill import (
    _OwnedProcessFinalizer,
    async_kill_process_tree,
    kill_process_tree,
)
from autoskillit.execution.process._process_monitor import (
    _has_active_api_connection,
    _has_active_child_processes,
    _has_active_execution_marker,
    _heartbeat,
    _session_log_monitor,
)
from autoskillit.execution.process._process_ownership import make_tracker
from autoskillit.execution.process._process_pty import pty_wrap_command
from autoskillit.execution.process._process_race import (
    RaceAccumulator,
    RaceSignals,
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
    from autoskillit.core import InspectorCallback, StreamParser
    from autoskillit.execution.linux_tracing import TraceTarget

logger = get_logger(__name__)


# Aggregate __all__ collects all public symbols from the execution sub-modules
# (_process_io, _process_jsonl, etc.) into a single facade. This keeps the
# internal sub-module split private — callers import from the facade, not from
# internal sub-module paths.
__all__ = [
    "DefaultSubprocessRunner",
    "_extract_stdout_session_id",
    "_resolve_session_id",
    "RaceAccumulator",
    "RaceSignals",
    "_has_active_api_connection",
    "_has_active_child_processes",
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
    proc: anyio.abc.Process,
    process_exited_event: anyio.Event,
    grace_seconds: float,
    proc_log: structlog.BoundLogger,
    finalizer: _OwnedProcessFinalizer | None = None,
) -> KillReason:
    """Single authorized executor for all kill decisions in run_managed_async.

    This is the ONLY function in process.py permitted to call
    async_kill_process_tree (enforced by test_no_direct_async_kill_process_tree_outside_executor).

    Returns the KillReason that surfaces to SubprocessResult.kill_reason.
    """
    match action:
        case TerminationAction.NO_KILL:
            if finalizer is not None:
                await finalizer.run()
            return KillReason.NATURAL_EXIT
        case TerminationAction.DRAIN_THEN_KILL_IF_ALIVE:
            with anyio.move_on_after(grace_seconds):
                await process_exited_event.wait()
            if proc.returncode is not None:
                proc_log.debug("natural_exit_after_drain", returncode=proc.returncode)
                return KillReason.NATURAL_EXIT
            proc_log.debug("grace_expired_killing", grace_seconds=grace_seconds)
            if finalizer is not None:
                await finalizer.run()
            else:
                await async_kill_process_tree(proc.pid)
            return KillReason.KILL_AFTER_COMPLETION
        case TerminationAction.IMMEDIATE_KILL:
            if finalizer is not None:
                await finalizer.run()
            else:
                await async_kill_process_tree(proc.pid)
            return KillReason.INFRA_KILL
        case _ as unreachable:
            assert_never(unreachable)


def _stdout_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _apply_lifecycle_completion_authority(
    termination: TerminationReason,
    channel_confirmation: ChannelConfirmation,
    lifecycle_decision: LifecycleDecision,
) -> tuple[TerminationReason, ChannelConfirmation]:
    """Require actor authorization before raw channel evidence can complete."""
    if lifecycle_decision is LifecycleDecision.ELIGIBLE:
        return TerminationReason.COMPLETED, ChannelConfirmation.CHANNEL_A
    if lifecycle_decision in {
        LifecycleDecision.CHILD_WORK_FAILED,
        LifecycleDecision.CATCH_UP_FAILED,
    }:
        return TerminationReason.HEALTH_INSPECTOR, ChannelConfirmation.UNMONITORED
    if channel_confirmation in {
        ChannelConfirmation.CHANNEL_A,
        ChannelConfirmation.CHANNEL_B,
    }:
        return TerminationReason.HEALTH_INSPECTOR, ChannelConfirmation.UNMONITORED
    return termination, channel_confirmation


async def _watch_process_with_lifecycle(
    proc: anyio.abc.Process,
    acc: RaceAccumulator,
    fact_send: anyio.abc.ObjectSendStream[object],
    stdout_path: Path,
    actor_decision_event: anyio.Event,
    trigger: anyio.Event,
    completion_drain_timeout: float,
    on_decision: Callable[[LifecycleDecision, CompletionCandidate | None], None],
) -> None:
    local_trigger = anyio.Event()
    await _watch_process(proc, acc, local_trigger)
    request_id = f"process-exit-{uuid.uuid4()}"
    await fact_send.send(
        ProcessExitFact(
            request_id=request_id,
            returncode=proc.returncode,
            required_channel_a_byte_offset=_stdout_size(stdout_path),
        )
    )
    with anyio.move_on_after(completion_drain_timeout) as drain_scope:
        await actor_decision_event.wait()
    if drain_scope.cancel_called and not actor_decision_event.is_set():
        on_decision(LifecycleDecision.CATCH_UP_FAILED, None)
    trigger.set()


async def _watch_session_log_with_lifecycle(
    *,
    session_log_dir: Path,
    completion_marker: str,
    stale_threshold: float,
    spawn_time: float,
    session_record_types: frozenset[str],
    pid: int,
    completion_drain_timeout: float,
    acc: RaceAccumulator,
    trigger: anyio.Event,
    channel_b_ready: anyio.Event,
    phase1_poll: float,
    phase2_poll: float,
    phase1_timeout: float,
    session_id_timeout: float,
    stdout_session_id_ready: anyio.Event,
    max_suppression_seconds: float | None,
    marker_dir: Path | None,
    marker_scope_session_id: str | None,
    stdout_path: Path,
    fact_send: anyio.abc.ObjectSendStream[object],
    reply_sends: dict[str, MemoryObjectSendStream[object]],
) -> None:
    local_trigger = anyio.Event()
    await _watch_session_log(
        session_log_dir,
        completion_marker,
        stale_threshold,
        spawn_time,
        session_record_types,
        pid,
        completion_drain_timeout,
        acc,
        local_trigger,
        channel_b_ready,
        phase1_poll,
        phase2_poll,
        phase1_timeout,
        session_id_timeout,
        stdout_session_id_ready,
        max_suppression_seconds,
        marker_dir,
        marker_scope_session_id,
    )
    if acc.channel_b_status is not ChannelBStatus.COMPLETION:
        trigger.set()
        return

    request_id = f"channel-b-{uuid.uuid4()}"
    reply_send, reply_receive = anyio.create_memory_object_stream[object](1)
    reply_sends[request_id] = reply_send
    try:
        await fact_send.send(
            ChannelBProposal(
                request_id=request_id,
                status="completion",
                session_id=acc.channel_b_session_id,
                byte_offset=0,
                required_byte_offset=_stdout_size(stdout_path),
                orphan_diagnostic=acc.channel_b_orphaned_tool_result,
            )
        )
        with anyio.move_on_after(completion_drain_timeout) as scope:
            await reply_receive.receive()
        if scope.cancel_called:
            with anyio.CancelScope(shield=True):
                await fact_send.send(CatchUpCancellationFact(request_id=request_id))
    finally:
        reply_sends.pop(request_id, None)
        await reply_send.aclose()
        await reply_receive.aclose()


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
    stream_parser: StreamParser | None = None,
    stream_parser_factory: StreamParserFactory | None = None,
    inspector_callback: InspectorCallback | None = None,
    workload_basenames: frozenset[str] | None = None,
    on_session_id_resolved: Callable[[str], None] | None = None,
    cleanup_budget_seconds: float = DEFAULT_CLEANUP_BUDGET_SECONDS,
) -> SubprocessResult:
    """Async subprocess execution with temp file I/O and process tree cleanup.

    Wires all lifecycle utilities together:
    1. create_temp_io for stdout/stderr/stdin
    2. optional PTY wrapping for TTY-dependent CLIs
    3. spawn with start_new_session=True
    4. two-channel race: proc.wait / stdout heartbeat / session log monitor
    5. async_kill_process_tree on failure/timeout/completion-detection
    6. read_temp_output for results
    7. cleanup temp files via context manager
    """
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
            _ownership_tracker = make_tracker()
            _ownership_tracker.register_root(
                proc.pid,
                read_starttime_ticks(proc.pid) or 0,
                psutil.Process(proc.pid).create_time(),
            )
            _finalizer = _OwnedProcessFinalizer(
                tracker=_ownership_tracker,
                budget_seconds=cleanup_budget_seconds,
            )

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
            actor_decision_event = anyio.Event()
            lifecycle_decision = LifecycleDecision.CONTINUE
            lifecycle_candidate: CompletionCandidate | None = None
            lifecycle_snapshot: ChildLifecycleSnapshot | None = None
            reply_sends: dict[str, MemoryObjectSendStream[object]] = {}
            fact_send, fact_receive = anyio.create_memory_object_stream[object](64)
            pump_command_send, pump_command_receive = anyio.create_memory_object_stream(8)

            lifecycle_enabled = stream_parser_factory is not None or stream_parser is not None
            if stream_parser_factory is not None:
                lifecycle_parser = stream_parser_factory()
            elif stream_parser is not None:
                lifecycle_parser = stream_parser
            else:
                lifecycle_parser = None

            def _record_stdout_session_id(resolved_session_id: str) -> None:
                if acc.stdout_session_id:
                    return
                acc.stdout_session_id = resolved_session_id
                stdout_session_id_ready.set()
                if on_session_id_resolved is not None:
                    on_session_id_resolved(resolved_session_id)

            def _record_lifecycle_snapshot(snapshot: ChildLifecycleSnapshot) -> None:
                nonlocal lifecycle_snapshot
                lifecycle_snapshot = snapshot

            def _record_lifecycle_decision(
                decision: LifecycleDecision,
                candidate: CompletionCandidate | None,
            ) -> None:
                nonlocal lifecycle_decision, lifecycle_candidate
                lifecycle_decision = decision
                lifecycle_candidate = candidate
                if decision is LifecycleDecision.ELIGIBLE:
                    acc.channel_a_confirmed = True
                actor_decision_event.set()
                trigger.set()

            def _reply_send_for_request(
                request_id: str,
            ) -> MemoryObjectSendStream[object] | None:
                return reply_sends.get(request_id)

            pump_state: ChannelAPumpState | None = None
            if lifecycle_parser is not None:
                pump_state = ChannelAPumpState(
                    completion_marker=completion_marker,
                    stdout_path=stdout_path,
                    on_session_id_resolved=_record_stdout_session_id,
                )
                bind_parser(pump_state, lifecycle_parser)

            async with anyio.create_task_group() as tg:
                if lifecycle_enabled and pump_state is not None:
                    tg.start_soon(
                        functools.partial(
                            run_lifecycle_actor,
                            completion_drain_timeout=completion_drain_timeout,
                            on_snapshot=_record_lifecycle_snapshot,
                        ),
                        fact_receive,
                        pump_command_send,
                        _reply_send_for_request,
                        _record_lifecycle_decision,
                    )
                    tg.start_soon(
                        functools.partial(run_channel_a_pump, poll_interval=_heartbeat_poll),
                        pump_state,
                        fact_send,
                        pump_command_receive,
                    )
                    tg.start_soon(
                        _watch_process_with_lifecycle,
                        proc,
                        acc,
                        fact_send,
                        stdout_path,
                        actor_decision_event,
                        trigger,
                        completion_drain_timeout,
                        _record_lifecycle_decision,
                    )
                    if session_log_dir is not None:
                        tg.start_soon(
                            functools.partial(
                                _watch_session_log_with_lifecycle,
                                session_log_dir=session_log_dir,
                                completion_marker=completion_marker,
                                stale_threshold=stale_threshold,
                                spawn_time=_spawn_time,
                                session_record_types=session_record_types,
                                pid=_observed_pid,
                                completion_drain_timeout=completion_drain_timeout,
                                acc=acc,
                                trigger=trigger,
                                channel_b_ready=channel_b_ready,
                                phase1_poll=_phase1_poll,
                                phase2_poll=_phase2_poll,
                                phase1_timeout=_phase1_timeout,
                                session_id_timeout=_session_id_timeout,
                                stdout_session_id_ready=stdout_session_id_ready,
                                max_suppression_seconds=max_suppression_seconds,
                                marker_dir=marker_dir,
                                marker_scope_session_id=marker_scope_session_id,
                                stdout_path=stdout_path,
                                fact_send=fact_send,
                                reply_sends=reply_sends,
                            )
                        )
                else:
                    tg.start_soon(_watch_process, proc, acc, trigger)
                    tg.start_soon(
                        functools.partial(
                            _watch_heartbeat,
                            stream_parser=stream_parser,
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
                tracing_handle = None
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
                        ),
                    )
                timeout_scope: anyio.CancelScope | None
                with anyio.move_on_after(timeout) as _ts:
                    timeout_scope_ref[0] = _ts
                    await trigger.wait()
                timeout_scope = timeout_scope_ref[0]
                if (
                    not lifecycle_enabled
                    and acc.process_exited
                    and acc.channel_b_status is None
                    and session_log_dir is not None
                ):
                    with anyio.move_on_after(completion_drain_timeout):
                        await channel_b_ready.wait()
                if (
                    lifecycle_enabled
                    and timeout_scope is not None
                    and timeout_scope.cancelled_caught
                ):
                    lifecycle_decision = LifecycleDecision.CATCH_UP_FAILED
                    actor_decision_event.set()
                if pump_state is not None:
                    pump_state.closed = True
                tg.cancel_scope.cancel()

            signals = acc.to_race_signals()
            termination, _channel_confirmation = resolve_termination(signals)
            if lifecycle_enabled:
                termination, _channel_confirmation = _apply_lifecycle_completion_authority(
                    termination,
                    _channel_confirmation,
                    lifecycle_decision,
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
            action = decide_termination_action(
                termination,
                timeout_fired=timeout_scope is not None and timeout_scope.cancelled_caught,
                process_exited=signals.process_exited,
            )
            proc_log.debug(
                "kill_decision",
                termination=str(termination),
                action=str(action),
                reason=str(action),
                process_exited=signals.process_exited,
                channel_a=signals.channel_a_confirmed,
                channel_b=signals.channel_b_status,
            )
            kill_reason = await execute_termination_action(
                action,
                proc=proc,
                process_exited_event=signals.process_exited_event,
                grace_seconds=natural_exit_grace_seconds,
                proc_log=proc_log,
                finalizer=_finalizer,
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
                kill_reason=kill_reason,
                tracked_comm=_tracked_comm,
                orphaned_tool_result=signals.channel_b_orphaned_tool_result,
                inspector_verdict=signals.inspector_verdict,
                cleanup_outcome=_finalizer.outcome,
                lifecycle_snapshot=lifecycle_snapshot,
                lifecycle_decision=lifecycle_decision,
                lifecycle_candidate=lifecycle_candidate,
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
            # Shielded cleanup: when a task is cancelled, the BaseException handler
            # runs with cancellation active. Without shielding, the await in
            # async_kill_process_tree would be immediately cancelled, leaking the
            # subprocess tree. CancelScope(shield=True) suspends the outer cancel
            # so cleanup completes before re-raising.
            with anyio.CancelScope(shield=True):
                if "tracing_handle" in locals() and tracing_handle is not None:
                    tracing_handle.stop()
                if "_finalizer" in locals():
                    await _finalizer.run()
                elif "proc" in locals() and proc.returncode is None:
                    await async_kill_process_tree(proc.pid)
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
    """Sync subprocess execution with temp file I/O and process tree cleanup.

    Same composition pattern as run_managed_async but uses subprocess.Popen
    with start_new_session=True. No channel monitoring — wall-clock timeout only.
    """
    with create_temp_io(input_data) as (stdout_file, stderr_file, stdin_path):
        stdout_path = Path(stdout_file.name)
        stderr_path = Path(stderr_file.name)

        # Build stdin handle — use DEVNULL when no input to prevent
        # inheriting parent's stdin (e.g. MCP server socket that never closes)
        stdin_handle = None
        if stdin_path is not None:
            stdin_handle = open(stdin_path)  # noqa: SIM115

        process = None
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

            termination = TerminationReason.NATURAL_EXIT
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                termination = TerminationReason.TIMED_OUT
                logger.warning(
                    "Process %d timed out after %ss, killing tree",
                    process.pid,
                    timeout,
                )
                kill_process_tree(process.pid)

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
            )
        except Exception:
            if process is not None and process.returncode is None:
                kill_process_tree(process.pid)
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
        stream_parser: StreamParser | None = None,
        stream_parser_factory: StreamParserFactory | None = None,
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
            stream_parser=stream_parser,
            stream_parser_factory=stream_parser_factory,
            completion_record_types=completion_record_types,
            session_record_types=session_record_types,
            inspector_callback=inspector_callback,
            workload_basenames=workload_basenames,
            on_session_id_resolved=on_session_id_resolved,
            cleanup_budget_seconds=cleanup_budget_seconds,
        )
