"""Race detection machinery for the anyio task group in run_managed_async."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import anyio
import anyio.abc

from autoskillit.core import ChannelConfirmation, TerminationReason, get_logger
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
    channel_b_status: str | None  # "completion", "stale", or None


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
    channel_b_status: str | None = None

    def to_race_signals(self) -> RaceSignals:
        return RaceSignals(
            process_exited=self.process_exited,
            process_returncode=self.process_returncode,
            channel_a_confirmed=self.channel_a_confirmed,
            channel_b_status=self.channel_b_status,
        )


async def _watch_process(
    proc: anyio.abc.Process,
    acc: RaceAccumulator,
    trigger: anyio.Event,
) -> None:
    """Wait for the subprocess to exit and deposit the process-exit signal."""
    await proc.wait()
    acc.process_exited = True
    acc.process_returncode = proc.returncode
    trigger.set()


async def _watch_heartbeat(
    stdout_path: Path,
    heartbeat_record_types: frozenset[str],
    acc: RaceAccumulator,
    trigger: anyio.Event,
    _poll_interval: float,
) -> None:
    """Poll stdout NDJSON for a result record and deposit the Channel A signal."""
    await _heartbeat(stdout_path, heartbeat_record_types, _poll_interval=_poll_interval)
    acc.channel_a_confirmed = True
    trigger.set()


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
) -> None:
    """Monitor the session JSONL log and deposit the Channel B signal.

    When the session reports completion (not stale), a drain-wait window
    is opened via anyio.move_on_after so Channel A can fire first if it
    is about to confirm. The trigger is set after the B signal is deposited.
    """
    result = await _session_log_monitor(
        session_log_dir,
        completion_marker,
        stale_threshold,
        spawn_time,
        session_record_types,
        pid=pid,
        _phase1_poll=_phase1_poll,
        _phase2_poll=_phase2_poll,
        _phase1_timeout=_phase1_timeout,
    )
    if result == "completion":
        # Drain-wait: give Channel A a window to confirm before Channel B wins.
        # move_on_after absorbs timeout; trigger may already be set if A fired.
        with anyio.move_on_after(completion_drain_timeout):
            await trigger.wait()
    # These writes execute atomically before any cancellation delivery:
    # there is no await between them and the function return.
    acc.channel_b_status = result
    channel_b_ready.set()
    trigger.set()


def resolve_termination(
    signals: RaceSignals,
) -> tuple[TerminationReason, ChannelConfirmation]:
    """Determine termination and channel from accumulated signals.

    Pure function: no side effects. Channel confirmation and termination
    reason are resolved independently so that simultaneous task completion
    never discards a channel signal.

    Priority for termination: process exit > stale > channel win.
    Channel confirmation is independent of termination.
    """
    # Channel confirmation: independent of termination reason
    if signals.channel_a_confirmed:
        channel = ChannelConfirmation.CHANNEL_A
    elif signals.channel_b_status == "completion":
        channel = ChannelConfirmation.CHANNEL_B
    else:
        channel = ChannelConfirmation.UNMONITORED

    # Termination reason: priority order
    if signals.process_exited:
        termination = TerminationReason.NATURAL_EXIT
    elif signals.channel_b_status == "stale":
        termination = TerminationReason.STALE
    elif signals.channel_a_confirmed:
        termination = TerminationReason.COMPLETED
    elif signals.channel_b_status == "completion":
        termination = TerminationReason.COMPLETED
    else:
        termination = TerminationReason.NATURAL_EXIT  # fallback

    return termination, channel
