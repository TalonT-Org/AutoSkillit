"""Race detection machinery for the anyio task group in run_managed_async."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

import anyio
import anyio.abc

from autoskillit.core import ChannelBStatus, ChannelConfirmation, TerminationReason, get_logger
from autoskillit.core import fast_loads as _fast_loads
from autoskillit.execution.process._process_monitor import (
    _has_active_api_connection,
    _has_active_child_processes,
    _has_active_dispatch_marker,
    _heartbeat,
    _session_log_monitor,
)

if TYPE_CHECKING:
    from autoskillit.core import StreamParser

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RaceSignals:
    """Accumulated signals produced by the anyio task group race in run_managed_async.

    Captures what happened without making any decisions about what it means.
    All fields are independent: multiple can be True simultaneously when tasks
    complete in the same event loop tick.

    ``process_exited_event`` is the same object as on RaceAccumulator — setting
    it on the accumulator automatically reflects here (shared reference).
    execute_termination_action awaits this event inside DRAIN_THEN_KILL_IF_ALIVE.
    """

    process_exited: bool
    process_returncode: int | None
    channel_a_confirmed: bool
    channel_b_status: ChannelBStatus | None
    channel_b_session_id: str = ""  # Claude Code session ID from JSONL filename stem, or ""
    stdout_session_id: str | None = None  # Session ID extracted from stdout type=system record
    idle_stall: bool = False
    channel_b_orphaned_tool_result: bool = False
    process_exited_event: anyio.Event = field(default_factory=anyio.Event)
    exit_snapshot: dict[str, object] | None = None


@dataclass
class RaceAccumulator:
    """Mutable signal accumulator for the anyio task group race in run_managed_async.

    Each watch coroutine writes exactly one field before setting the trigger.
    In async cooperative concurrency there are no concurrent writes — only one
    coroutine runs between yield points. to_race_signals() converts to the
    frozen RaceSignals consumed by resolve_termination.

    ``process_exited_event`` is set by _watch_process BEFORE setting trigger,
    so downstream code can await it as a race primitive. It is passed (by reference)
    to execute_termination_action to support the DRAIN_THEN_KILL_IF_ALIVE window.
    """

    process_exited: bool = False
    process_returncode: int | None = None
    channel_a_confirmed: bool = False
    channel_b_status: ChannelBStatus | None = None
    channel_b_session_id: str = ""
    stdout_session_id: str | None = None
    idle_stall: bool = False
    channel_b_orphaned_tool_result: bool = False
    process_exited_event: anyio.Event = field(default_factory=anyio.Event)
    exit_snapshot: dict[str, object] | None = None

    def to_race_signals(self) -> RaceSignals:
        return RaceSignals(
            process_exited=self.process_exited,
            process_returncode=self.process_returncode,
            channel_a_confirmed=self.channel_a_confirmed,
            channel_b_status=self.channel_b_status,
            channel_b_session_id=self.channel_b_session_id,
            stdout_session_id=self.stdout_session_id,
            idle_stall=self.idle_stall,
            channel_b_orphaned_tool_result=self.channel_b_orphaned_tool_result,
            process_exited_event=self.process_exited_event,
            exit_snapshot=self.exit_snapshot,
        )


async def _watch_process(
    proc: anyio.abc.Process,
    acc: RaceAccumulator,
    trigger: anyio.Event,
) -> None:
    """Wait for the subprocess to exit and deposit the process-exit signal.

    Ordering guarantee: ``acc.process_exited_event`` is set BEFORE ``trigger``
    so that execute_termination_action's DRAIN_THEN_KILL_IF_ALIVE path can
    await the event inside the drain window.
    """
    await proc.wait()
    logger.debug("process_exited", pid=proc.pid, returncode=proc.returncode)
    # Exit snapshot: best-effort capture at exact exit time.
    # waitpid() reaps the process atomically, so /proc/[pid] is already gone for
    # normally-exiting processes — acc.exit_snapshot will be None in most cases.
    try:
        # Deferred import: linux_tracing depends on psutil and reads /proc, which is
        # Linux-only. Importing at module level would fail on non-Linux platforms where
        # LINUX_TRACING_AVAILABLE is False. The bare except below degrades gracefully.
        from autoskillit.execution.linux_tracing import read_proc_snapshot

        snap = read_proc_snapshot(proc.pid)
        if snap is not None:
            acc.exit_snapshot = {**asdict(snap), "event": "exit_snapshot"}
    except Exception:
        logger.debug("exit_snapshot_failed", pid=proc.pid, exc_info=True)
    acc.process_exited = True
    acc.process_returncode = proc.returncode
    acc.process_exited_event.set()
    trigger.set()


async def _watch_heartbeat(
    stdout_path: Path,
    heartbeat_record_types: frozenset[str],
    completion_marker: str,
    acc: RaceAccumulator,
    trigger: anyio.Event,
    stream_parser: StreamParser | None = None,
    _poll_interval: float = 0.5,
) -> None:
    """Poll stdout NDJSON for a result record and deposit the Channel A signal."""
    await _heartbeat(
        stdout_path,
        heartbeat_record_types,
        completion_marker=completion_marker,
        _poll_interval=_poll_interval,
        stream_parser=stream_parser,
    )
    logger.debug(
        "channel_a_confirmed",
        stdout_path=str(stdout_path),
        record_types=list(heartbeat_record_types),
    )
    acc.channel_a_confirmed = True
    trigger.set()


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
) -> None:
    """Kill the child if stdout stops growing for idle_output_timeout seconds.

    Orthogonal to Channel A/B: NOT suppressed by active API connections.
    Monitors raw byte count (st_size), not JSONL record structure.

    When ``marker_dir`` is provided and an active dispatch marker exists, the
    idle stall is suppressed for up to ``max_suppression_seconds`` to allow
    in-flight dispatches to complete. Growth in stdout resets the suppression
    timer, giving a fresh window for subsequent idle periods.
    """
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
            continue
        if current_size > last_size:
            last_size = current_size
            last_growth_time = _time.monotonic()
            suppression_start_marker = None
        elif _time.monotonic() - last_growth_time >= idle_output_timeout:
            if marker_dir is not None and _has_active_dispatch_marker(
                marker_dir, session_id=session_id
            ):
                now = _time.monotonic()
                if suppression_start_marker is None:
                    suppression_start_marker = now
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
            logger.warning(
                "stdout idle for %ss — firing IDLE_STALL",
                idle_output_timeout,
            )
            acc.idle_stall = True
            trigger.set()
            return


async def _watch_child_activity(
    pid: int,
    timeout_scope_ref: list[anyio.CancelScope | None],
    max_extension_seconds: float,
    trigger: anyio.Event,
    _poll_interval: float = 30.0,
) -> None:
    """Extend the wall-clock CancelScope.deadline when child processes are active.

    Polls _has_active_child_processes and _has_active_api_connection every
    _poll_interval seconds. When either returns True, pushes
    timeout_scope.deadline forward (up to max_extension_seconds beyond the
    original deadline).

    Terminates when trigger fires (session completed normally). Crash is
    fail-closed — anyio propagates exceptions in the task group, cancelling
    siblings.
    """
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

        active = _has_active_child_processes(pid) or _has_active_api_connection(pid)
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


async def _extract_stdout_session_id(
    stdout_path: Path,
    acc: RaceAccumulator,
    ready: anyio.Event,
    stream_parser: StreamParser | None = None,
    _poll_interval: float = 0.3,
    _timeout: float = 10.0,
) -> None:
    """Extract session ID from stdout type=system record and deposit on accumulator.

    The Claude CLI writes a type=system record early in startup that contains the
    session ID used as the JSONL filename stem. By extracting it from stdout (owned
    by this session via create_temp_io), ownership is transitive — no discovery race.
    """
    import time as _time

    start = _time.monotonic()
    scan_pos = 0
    while _time.monotonic() - start < _timeout:
        await anyio.sleep(_poll_interval)
        try:
            raw = stdout_path.read_bytes()
        except OSError:
            continue
        new_raw = raw[scan_pos:]
        scan_pos = len(raw)
        if not new_raw:
            continue
        content = new_raw.decode("utf-8", errors="replace")
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            sid: str | None = None
            if stream_parser is not None:
                event = stream_parser.parse_line(line)
                if event is not None:
                    sid = event.session_id
            else:
                try:
                    obj = _fast_loads(line)
                except ValueError:
                    continue
                if (
                    isinstance(obj, dict)
                    and obj.get("type") == "system"
                    and obj.get("subtype") == "init"
                ):
                    raw_sid = obj.get("session_id")
                    if isinstance(raw_sid, str):
                        sid = raw_sid
            if sid:
                acc.stdout_session_id = sid
                logger.debug("stdout_session_id_extracted", session_id=sid)
                ready.set()
                return
    logger.debug("stdout_session_id_extraction_timeout", timeout=_timeout)
    ready.set()


async def _watch_session_log(
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
    _phase1_poll: float,
    _phase2_poll: float,
    _phase1_timeout: float,
    _session_id_timeout: float = 1.0,
    stdout_session_id_ready: anyio.Event | None = None,
    max_suppression_seconds: float | None = None,
    marker_dir: Path | None = None,
    session_id: str | None = None,
) -> None:
    """Monitor the session JSONL log and deposit the Channel B signal.

    When the session reports completion (not stale), a drain-wait window
    is opened via anyio.move_on_after so Channel A can fire first if it
    is about to confirm. The trigger is set after the B signal is deposited.

    If ``stdout_session_id_ready`` is provided, waits briefly for session ID
    extraction before starting Phase 1 to enable identity-based JSONL selection.
    """
    if stdout_session_id_ready is not None:
        with anyio.move_on_after(_session_id_timeout):
            await stdout_session_id_ready.wait()

    _monitor_kwargs: dict[str, object] = {
        "pid": pid,
        "_phase1_poll": _phase1_poll,
        "_phase2_poll": _phase2_poll,
        "_phase1_timeout": _phase1_timeout,
        "expected_session_id": acc.stdout_session_id,
    }
    if max_suppression_seconds is not None:
        _monitor_kwargs["max_suppression_seconds"] = max_suppression_seconds
    if marker_dir is not None:
        _monitor_kwargs["marker_dir"] = marker_dir
    if session_id is not None:
        _monitor_kwargs["caller_session_id"] = session_id
    monitor_result = await _session_log_monitor(
        session_log_dir,
        completion_marker,
        stale_threshold,
        spawn_time,
        session_record_types,
        **_monitor_kwargs,  # type: ignore[arg-type]
    )
    if monitor_result.status == ChannelBStatus.COMPLETION:
        # Drain-wait: give Channel A a window to confirm before Channel B wins.
        # move_on_after absorbs timeout; trigger may already be set if A fired.
        with anyio.move_on_after(completion_drain_timeout):
            await trigger.wait()
        logger.debug("channel_b_drain_complete", trigger_was_set=trigger.is_set())
    logger.debug(
        "channel_b_result",
        status=monitor_result.status,
        session_id=monitor_result.session_id,
        drain_window=monitor_result.status == ChannelBStatus.COMPLETION,
    )
    # These writes execute atomically before any cancellation delivery:
    # there is no await between them and the function return.
    acc.channel_b_status = monitor_result.status
    acc.channel_b_session_id = monitor_result.session_id
    acc.channel_b_orphaned_tool_result = monitor_result.orphaned_tool_result
    channel_b_ready.set()
    trigger.set()


def resolve_termination(
    signals: RaceSignals,
) -> tuple[TerminationReason, ChannelConfirmation]:
    """Determine termination and channel from accumulated signals.

    Pure function: no side effects. Channel confirmation and termination
    reason are resolved independently so that simultaneous task completion
    never discards a channel signal.

    Priority for termination: process exit > idle stall > stale > channel win.
    Channel confirmation is independent of termination.

    Exhaustive match over ChannelBStatus ensures mypy flags any new member
    that is added without updating the resolution logic.
    """
    # Channel confirmation: independent of termination reason
    if signals.channel_a_confirmed:
        channel = ChannelConfirmation.CHANNEL_A
    else:
        match signals.channel_b_status:
            case ChannelBStatus.COMPLETION:
                channel = ChannelConfirmation.CHANNEL_B
            case ChannelBStatus.STALE | None:
                channel = ChannelConfirmation.UNMONITORED
            case ChannelBStatus.DIR_MISSING:
                channel = ChannelConfirmation.DIR_MISSING
            case _ as unreachable:
                assert_never(unreachable)

    # Termination reason: priority order (process exit > idle stall > stale > channel win)
    if signals.process_exited:
        termination = TerminationReason.NATURAL_EXIT
    elif signals.idle_stall:
        termination = TerminationReason.IDLE_STALL
    else:
        match signals.channel_b_status:
            case ChannelBStatus.STALE | ChannelBStatus.DIR_MISSING:
                # DIR_MISSING maps to STALE: both represent inconclusive monitoring
                # that triggered an external kill, not a clean process exit.
                # TerminationReason does not need a DIR_MISSING variant because
                # downstream consumers only care whether the process exited cleanly
                # (NATURAL_EXIT) or was forcibly terminated (STALE/COMPLETED).
                # The DIR_MISSING structural distinction is preserved at the
                # ChannelConfirmation level for recovery-gate decisions.
                termination = TerminationReason.STALE
            case ChannelBStatus.COMPLETION:
                termination = TerminationReason.COMPLETED
            case None:
                if signals.channel_a_confirmed:
                    termination = TerminationReason.COMPLETED
                else:
                    termination = TerminationReason.NATURAL_EXIT  # fallback
            case _ as unreachable:
                assert_never(unreachable)

    logger.debug(
        "resolve_termination",
        process_exited=signals.process_exited,
        process_returncode=signals.process_returncode,
        channel_a_confirmed=signals.channel_a_confirmed,
        channel_b_status=signals.channel_b_status,
        channel_b_session_id=signals.channel_b_session_id,
        idle_stall=signals.idle_stall,
        resolved_termination=str(termination),
        resolved_channel=str(channel),
    )
    return termination, channel
