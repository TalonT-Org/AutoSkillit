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
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from autoskillit.core import ChannelConfirmation, SubprocessResult, TerminationReason, get_logger
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
    _heartbeat,
    _session_log_monitor,
)
from autoskillit.execution._process_pty import pty_wrap_command
from autoskillit.execution._process_race import (
    RaceAccumulator,
    RaceSignals,
    _watch_heartbeat,
    _watch_process,
    _watch_session_log,
    resolve_termination,
)

if TYPE_CHECKING:
    from autoskillit.config import LinuxTracingConfig

logger = get_logger(__name__)

# Re-export all public symbols so callers using `from autoskillit.execution.process import X`
# continue to work without modification (P8-2: backward-compatible public surface).
__all__ = [
    "DefaultSubprocessRunner",
    "RaceAccumulator",
    "RaceSignals",
    "_has_active_api_connection",
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
    "kill_process_tree",
    "pty_wrap_command",
    "read_temp_output",
    "resolve_termination",
    "run_managed_async",
    "run_managed_sync",
]


async def run_managed_async(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
    input_data: str | None = None,
    env: dict[str, str] | None = None,
    pty_mode: bool = False,
    heartbeat_record_types: frozenset[str] = frozenset({"result"}),
    session_log_dir: Path | None = None,
    completion_marker: str = "",
    stale_threshold: float = 1200,
    session_record_types: frozenset[str] = frozenset({"assistant"}),
    completion_drain_timeout: float = 5.0,
    linux_tracing_config: LinuxTracingConfig | None = None,
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

            termination = TerminationReason.NATURAL_EXIT
            _channel_confirmation = ChannelConfirmation.UNMONITORED

            proc_log = logger.bind(pid=proc.pid)
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
            timeout_scope = None  # bound inside task group body; initialized for safety

            async with anyio.create_task_group() as tg:
                tg.start_soon(_watch_process, proc, acc, trigger)
                tg.start_soon(
                    _watch_heartbeat,
                    stdout_path,
                    heartbeat_record_types,
                    acc,
                    trigger,
                    _heartbeat_poll,
                )
                if session_log_dir is not None:
                    tg.start_soon(
                        _watch_session_log,
                        session_log_dir,
                        completion_marker,
                        stale_threshold,
                        time.time(),
                        session_record_types,
                        proc.pid,
                        completion_drain_timeout,
                        acc,
                        trigger,
                        channel_b_ready,
                        _phase1_poll,
                        _phase2_poll,
                        _phase1_timeout,
                    )
                tracing_handle = None
                if linux_tracing_config is not None:
                    from autoskillit.execution.linux_tracing import start_linux_tracing

                    tracing_handle = start_linux_tracing(
                        pid=proc.pid,
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

                accumulated = await tracing_handle.stop()
                final_snap = read_proc_snapshot(proc.pid)
                if final_snap:
                    accumulated.append(final_snap)
                snapshots_data = [s.__dict__ for s in accumulated]

            if timeout_scope.cancelled_caught:
                termination = TerminationReason.TIMED_OUT
                proc_log.debug("kill_decision", reason="timeout", timeout=timeout)
                logger.warning("Process %d timed out after %ss, killing tree", proc.pid, timeout)
                await async_kill_process_tree(proc.pid)
            elif signals.process_exited:
                proc_log.debug(
                    "kill_decision",
                    reason="natural_exit",
                    returncode=signals.process_returncode,
                )
            elif termination == TerminationReason.STALE:
                proc_log.debug("kill_decision", reason="stale", stale_threshold=stale_threshold)
                logger.warning("Session stale for %ss, killing tree", stale_threshold)
                await async_kill_process_tree(proc.pid)
            else:
                proc_log.debug(
                    "kill_decision",
                    reason="channel_won",
                    channel_a=signals.channel_a_confirmed,
                    channel_b=signals.channel_b_status,
                )
                # Channel A or B won; process still alive — kill immediately.
                await async_kill_process_tree(proc.pid)

            # Flush and close before reading
            stdout_file.close()
            stderr_file.close()

            stdout, stderr = read_temp_output(stdout_path, stderr_path)

            sub_result = SubprocessResult(
                returncode=proc.returncode if proc.returncode is not None else -1,
                stdout=stdout,
                stderr=stderr,
                termination=termination,
                pid=proc.pid,
                channel_confirmation=_channel_confirmation,
                proc_snapshots=snapshots_data,
                channel_b_session_id=signals.channel_b_session_id,
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
    env: dict[str, str] | None = None,
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
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = False,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
        linux_tracing_config: LinuxTracingConfig | None = None,
    ) -> SubprocessResult:
        return await run_managed_async(
            cmd,
            cwd=cwd,
            timeout=timeout,
            stale_threshold=stale_threshold,
            completion_marker=completion_marker,
            session_log_dir=session_log_dir,
            pty_mode=pty_mode,
            input_data=input_data,
            completion_drain_timeout=completion_drain_timeout,
            linux_tracing_config=linux_tracing_config,
        )
