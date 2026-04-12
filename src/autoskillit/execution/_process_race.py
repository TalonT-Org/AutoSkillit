"""Race detection machinery for the anyio task group in run_managed_async."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

import anyio
import anyio.abc

from autoskillit.core import ChannelBStatus, ChannelConfirmation, TerminationReason, get_logger
from autoskillit.execution._process_monitor import _heartbeat, _session_log_monitor

logger = get_logger(__name__)


@dataclass(frozen=True)
class RaceSignals:
    """Accumulated signals produced by the anyio task group race in run_managed_async.

    Captures what happened without making any decisions about what it means.
    All fields are independent: multiple can be True simultaneously when tasks
    complete in the same event loop tick.
    """

    process_exited: bool
    process_returncode: int | None
    channel_a_confirmed: bool
    channel_b_status: ChannelBStatus | None
    channel_b_session_id: str = ""  # Claude Code session ID from JSONL filename stem, or ""
    stdout_session_id: str | None = None  # Session ID extracted from stdout type=system record
    idle_stall: bool = False


@dataclass
class RaceAccumulator:
    """Mutable signal accumulator for the anyio task group race in run_managed_async.

    Each watch coroutine writes exactly one field before setting the trigger.
    In async cooperative concurrency there are no concurrent writes — only one
    coroutine runs between yield points. to_race_signals() converts to the
    frozen RaceSignals consumed by resolve_termination.
    """

    process_exited: bool = False
    process_returncode: int | None = None
    channel_a_confirmed: bool = False
    channel_b_status: ChannelBStatus | None = None
    channel_b_session_id: str = ""
    stdout_session_id: str | None = None
    idle_stall: bool = False

    def to_race_signals(self) -> RaceSignals:
        return RaceSignals(
            process_exited=self.process_exited,
            process_returncode=self.process_returncode,
            channel_a_confirmed=self.channel_a_confirmed,
            channel_b_status=self.channel_b_status,
            channel_b_session_id=self.channel_b_session_id,
            stdout_session_id=self.stdout_session_id,
            idle_stall=self.idle_stall,
        )


async def _watch_process(
    proc: anyio.abc.Process,
    acc: RaceAccumulator,
    trigger: anyio.Event,
) -> None:
    """Wait for the subprocess to exit and deposit the process-exit signal."""
    await proc.wait()
    logger.debug("process_exited", pid=proc.pid, returncode=proc.returncode)
    acc.process_exited = True
    acc.process_returncode = proc.returncode
    trigger.set()


async def _watch_heartbeat(
    stdout_path: Path,
    heartbeat_record_types: frozenset[str],
    completion_marker: str,
    acc: RaceAccumulator,
    trigger: anyio.Event,
    _poll_interval: float,
) -> None:
    """Poll stdout NDJSON for a result record and deposit the Channel A signal."""
    await _heartbeat(
        stdout_path,
        heartbeat_record_types,
        completion_marker=completion_marker,
        _poll_interval=_poll_interval,
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
) -> None:
    """Kill the child if stdout stops growing for idle_output_timeout seconds.

    Orthogonal to Channel A/B: NOT suppressed by active API connections.
    Monitors raw byte count (st_size), not JSONL record structure.
    """
    import time as _time

    last_size: int = 0
    last_growth_time: float = _time.monotonic()
    while True:
        await anyio.sleep(_poll_interval)
        try:
            current_size = stdout_path.stat().st_size
        except OSError:
            continue
        if current_size > last_size:
            last_size = current_size
            last_growth_time = _time.monotonic()
        elif _time.monotonic() - last_growth_time >= idle_output_timeout:
            logger.warning(
                "stdout idle for %ss — firing IDLE_STALL",
                idle_output_timeout,
            )
            acc.idle_stall = True
            trigger.set()
            return


async def _extract_stdout_session_id(
    stdout_path: Path,
    acc: RaceAccumulator,
    ready: anyio.Event,
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
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict) and obj.get("type") == "system":
                sid = obj.get("session_id")
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
    stdout_session_id_ready: anyio.Event | None = None,
    max_suppression_seconds: float | None = None,
) -> None:
    """Monitor the session JSONL log and deposit the Channel B signal.

    When the session reports completion (not stale), a drain-wait window
    is opened via anyio.move_on_after so Channel A can fire first if it
    is about to confirm. The trigger is set after the B signal is deposited.

    If ``stdout_session_id_ready`` is provided, waits briefly for session ID
    extraction before starting Phase 1 to enable identity-based JSONL selection.
    """
    if stdout_session_id_ready is not None:
        with anyio.move_on_after(1.0):
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
