"""Subprocess lifecycle utilities providing pipe-blocking immunity.

Shared building blocks for all subprocess-spawning code in the project.
Uses temp file I/O (not pipes) to eliminate FD-inheritance blocking, and
psutil-based process tree cleanup with SIGTERM→SIGKILL escalation.

Two composed functions wire the utilities together correctly:
- ``run_managed_async`` for async callers
- ``run_managed_sync`` for sync callers
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

import anyio
import anyio.abc

from autoskillit.core import (
    ChannelConfirmation,
    KillReason,
    SubprocessResult,
    TerminationAction,
    TerminationReason,
    get_logger,
)
from autoskillit.execution._process_io import create_temp_io, read_temp_output
from autoskillit.execution._process_jsonl import (
    _jsonl_contains_marker,
    _jsonl_has_record_type,
    _marker_is_standalone,
)
from autoskillit.execution._process_kill import (
    _wait_process_dead,
    async_kill_process_tree,
    kill_process_tree,
)
from autoskillit.execution._process_monitor import (
    _has_active_api_connection,
    _has_active_child_processes,
    _heartbeat,
    _session_log_monitor,
)
from autoskillit.execution._process_pty import pty_wrap_command
from autoskillit.execution._process_race import (
    RaceAccumulator,
    RaceSignals,
    _extract_stdout_session_id,
    _watch_heartbeat,
    _watch_process,
    _watch_session_log,
    _watch_stdout_idle,
    resolve_termination,
)

if TYPE_CHECKING:
    import structlog

    from autoskillit.config import LinuxTracingConfig
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
    "_heartbeat",
    "_jsonl_contains_marker",
    "_jsonl_has_record_type",
    "_marker_is_standalone",
    "_session_log_monitor",
    "_wait_process_dead",
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
        case TerminationReason.NATURAL_EXIT:
            return TerminationAction.NO_KILL
        case TerminationReason.COMPLETED:
            return TerminationAction.DRAIN_THEN_KILL_IF_ALIVE
        case TerminationReason.IDLE_STALL | TerminationReason.STALE | TerminationReason.TIMED_OUT:
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
) -> KillReason:
    """Single authorized executor for all kill decisions in run_managed_async.

    This is the ONLY function in process.py permitted to call
    async_kill_process_tree (enforced by test_no_direct_async_kill_process_tree_outside_executor).

    Returns the KillReason that surfaces to SubprocessResult.kill_reason.
    """
    match action:
        case TerminationAction.NO_KILL:
            return KillReason.NATURAL_EXIT
        case TerminationAction.DRAIN_THEN_KILL_IF_ALIVE:
            with anyio.move_on_after(grace_seconds):
                await process_exited_event.wait()
            if proc.returncode is not None:
                proc_log.debug("natural_exit_after_drain", returncode=proc.returncode)
                return KillReason.NATURAL_EXIT
            proc_log.debug("grace_expired_killing", grace_seconds=grace_seconds)
            await async_kill_process_tree(proc.pid)
            return KillReason.KILL_AFTER_COMPLETION
        case TerminationAction.IMMEDIATE_KILL:
            await async_kill_process_tree(proc.pid)
            return KillReason.INFRA_KILL
        case _ as unreachable:
            assert_never(unreachable)


async def run_managed_async(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
    input_data: str | None = None,
    env: Mapping[str, str] | None = None,
    pty_mode: bool = False,
    heartbeat_record_types: frozenset[str] = frozenset({"result"}),
    session_log_dir: Path | None = None,
    completion_marker: str = "",
    stale_threshold: float = 1200,
    session_record_types: frozenset[str] = frozenset({"assistant"}),
    completion_drain_timeout: float = 5.0,
    natural_exit_grace_seconds: float = 3.0,
    linux_tracing_config: LinuxTracingConfig | None = None,
    idle_output_timeout: float | None = None,
    max_suppression_seconds: float | None = None,
    _phase1_poll: float = 1.0,
    _phase2_poll: float = 2.0,
    _heartbeat_poll: float = 0.5,
    _phase1_timeout: float = 30.0,
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
            proc = await anyio.open_process(
                cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                cwd=cwd,
                env=env,
                start_new_session=True,
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
                    _target = resolve_trace_target(
                        root_pid=proc.pid,
                        expected_basename=_workload_basename,
                        timeout=2.0,
                    )
                else:
                    # Non-PTY mode: proc.pid IS the workload (direct child)
                    _target = trace_target_from_pid(proc.pid)
                assert _target is not None
                _observed_pid = _target.pid
                _tracked_comm = _target.comm

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
            timeout_scope = None  # bound inside task group body; initialized for safety

            async with anyio.create_task_group() as tg:
                tg.start_soon(_watch_process, proc, acc, trigger)
                tg.start_soon(
                    _watch_heartbeat,
                    stdout_path,
                    heartbeat_record_types,
                    completion_marker,
                    acc,
                    trigger,
                    _heartbeat_poll,
                )
                if session_log_dir is not None:
                    tg.start_soon(
                        _extract_stdout_session_id,
                        stdout_path,
                        acc,
                        stdout_session_id_ready,
                    )
                    tg.start_soon(
                        _watch_session_log,
                        session_log_dir,
                        completion_marker,
                        stale_threshold,
                        time.time(),
                        session_record_types,
                        _observed_pid,
                        completion_drain_timeout,
                        acc,
                        trigger,
                        channel_b_ready,
                        _phase1_poll,
                        _phase2_poll,
                        _phase1_timeout,
                        stdout_session_id_ready,
                        max_suppression_seconds,
                    )
                if idle_output_timeout is not None and idle_output_timeout > 0:
                    tg.start_soon(
                        _watch_stdout_idle,
                        stdout_path,
                        idle_output_timeout,
                        acc,
                        trigger,
                    )
                tracing_handle = None
                if linux_tracing_config is not None and _target is not None:
                    from autoskillit.execution.linux_tracing import start_linux_tracing

                    tracing_handle = start_linux_tracing(
                        target=_target,
                        config=linux_tracing_config,
                        tg=tg,
                    )
                with anyio.move_on_after(timeout) as timeout_scope:
                    await trigger.wait()
                # Symmetric drain: if the process exited before Channel B deposited,
                # give the session monitor a bounded window to complete its current
                # poll cycle and deposit its signal.
                if (
                    acc.process_exited
                    and acc.channel_b_status is None
                    and session_log_dir is not None
                ):
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

            signals = acc.to_race_signals()
            termination, _channel_confirmation = resolve_termination(signals)

            snapshots_data: list[dict[str, object]] | None = None
            if tracing_handle is not None:
                from autoskillit.execution.linux_tracing import read_proc_snapshot

                accumulated = tracing_handle.stop()
                final_snap = read_proc_snapshot(_observed_pid)
                if final_snap:
                    accumulated.append(final_snap)
                snapshots_data = [s.__dict__ for s in accumulated]

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
            # Ensure cleanup on unexpected errors (including CancelledError)
            if "tracing_handle" in locals() and tracing_handle is not None:
                tracing_handle.stop()  # idempotent: flushes and closes trace file
            if "proc" in locals() and proc.returncode is None:
                kill_process_tree(proc.pid)
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
            process = subprocess.Popen(
                cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                cwd=cwd,
                env=env,
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
        )
