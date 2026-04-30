"""Retry FSM sub-module for headless session adjudication."""

from __future__ import annotations

from typing import assert_never

from autoskillit.core import (
    ChannelConfirmation,
    CliSubtype,
    RetryReason,
    TerminationReason,
    get_logger,
)
from autoskillit.execution._session_model import ClaudeSessionResult

logger = get_logger(__name__)

__all__ = ["_KILL_ANOMALY_SUBTYPES", "_is_kill_anomaly", "_compute_retry"]


_KILL_ANOMALY_SUBTYPES: frozenset[CliSubtype] = frozenset(
    {
        CliSubtype.UNPARSEABLE,  # killed mid-write → partial NDJSON
        CliSubtype.EMPTY_OUTPUT,  # killed before any stdout was written
        CliSubtype.INTERRUPTED,  # killed mid-generation → real Claude CLI subtype
    }
)


def _is_kill_anomaly(session: ClaudeSessionResult) -> bool:
    """True if the session result looks like a kill-induced incomplete flush.

    Covers anomalies from both infrastructure kills (COMPLETED) and voluntary
    self-exits (NATURAL_EXIT with returncode==0). Callers must apply their own
    termination-specific discriminators before calling this function.

    Covers:
    - unparseable: process killed mid-write, stdout is partial NDJSON
    - empty_output: process killed before any stdout was written
    - interrupted: process killed mid-generation (real Claude CLI subtype)
    - success with empty result: kill occurred after result record was written
      but with empty content (Channel B / Channel A drain-race, or
      CLAUDE_CODE_EXIT_AFTER_STOP_DELAY timer-based self-exit)
    """
    if session.subtype in _KILL_ANOMALY_SUBTYPES:
        return True
    if session.subtype == CliSubtype.SUCCESS and not session.result.strip():
        return True
    return False


def _compute_retry(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
    completion_marker: str = "",
) -> tuple[bool, RetryReason]:
    """Compute whether the session result warrants a retry.

    Phase 1: API-level signals are termination-agnostic.
    Phase 2: Exhaustive match dispatch over TerminationReason ensures mypy
             flags any unhandled value when the enum is extended.

    When ``channel_confirmation=CHANNEL_B`` and ``termination=COMPLETED``,
    the provenance bypass applies: Channel B's session-JSONL signal is
    authoritative, so kill-anomaly appearance is a drain-race artifact.
    No retry is needed.
    """
    # Phase 1: API-level retry signals (context exhaustion, max_turns)
    if session.needs_retry:
        logger.debug("compute_retry_api_signal", needs_retry=True, reason="resume")
        return True, RetryReason.RESUME

    # Phase 2: Exhaustive termination dispatch
    match termination:
        case TerminationReason.NATURAL_EXIT:
            if channel_confirmation in (
                ChannelConfirmation.CHANNEL_A,
                ChannelConfirmation.CHANNEL_B,
            ):
                logger.debug(
                    "compute_retry_result",
                    termination="NATURAL_EXIT",
                    channel=str(channel_confirmation),
                    needs_retry=False,
                )
                return False, RetryReason.NONE
            if returncode == 0 and _is_kill_anomaly(session):
                reason = (
                    RetryReason.RESUME
                    if session._is_context_exhausted()
                    else RetryReason.EMPTY_OUTPUT
                )
                logger.debug(
                    "compute_retry_result",
                    termination="NATURAL_EXIT",
                    channel=str(channel_confirmation),
                    needs_retry=True,
                    kill_anomaly=True,
                    context_exhausted=reason == RetryReason.RESUME,
                )
                return True, reason
            # EARLY_STOP: model produced substantive output but stopped before
            # emitting the completion marker (text-then-tool boundary).
            if (
                returncode == 0
                and session.subtype == CliSubtype.SUCCESS
                and session.result.strip()
                and completion_marker
                and completion_marker not in session.result
            ):
                skill_tool_calls = [t for t in session.tool_uses if t.get("name") == "Skill"]
                logger.debug(
                    "compute_retry_early_stop",
                    termination="NATURAL_EXIT",
                    has_skill_calls=bool(skill_tool_calls),
                    skill_call_count=len(skill_tool_calls),
                )
                return True, RetryReason.EARLY_STOP
            logger.debug(
                "compute_retry_result",
                termination="NATURAL_EXIT",
                channel=str(channel_confirmation),
                needs_retry=False,
            )
            return False, RetryReason.NONE

        case TerminationReason.COMPLETED:
            # Infrastructure killed the process. SIGTERM/SIGKILL produce nonzero
            # returncode by design — do not gate on returncode here.
            # Exhaustive ChannelConfirmation dispatch (ARCH-007 extension):
            match channel_confirmation:
                case ChannelConfirmation.CHANNEL_B:
                    # Channel B is authoritative — kill-anomaly appearance is
                    # a drain-race artifact, not a real incomplete flush.
                    logger.debug(
                        "compute_retry_result",
                        termination="COMPLETED",
                        channel="CHANNEL_B",
                        needs_retry=False,
                    )
                    return False, RetryReason.NONE
                case (
                    ChannelConfirmation.CHANNEL_A
                    | ChannelConfirmation.UNMONITORED
                    | ChannelConfirmation.DIR_MISSING
                ):
                    is_anomaly = _is_kill_anomaly(session)
                    logger.debug(
                        "compute_retry_result",
                        termination="COMPLETED",
                        channel=str(channel_confirmation),
                        needs_retry=is_anomaly,
                        kill_anomaly=is_anomaly,
                    )
                    if is_anomaly:
                        return True, RetryReason.RESUME
                    return False, RetryReason.NONE
                case _ as _unreachable_cc:
                    assert_never(_unreachable_cc)

        case TerminationReason.STALE:
            # _build_skill_result intercepts STALE before calling _compute_retry.
            # Explicit arm exists for exhaustiveness; unreachable in production.
            logger.debug("compute_retry_result", termination="STALE", needs_retry=False)
            return False, RetryReason.NONE

        case TerminationReason.IDLE_STALL:
            # _build_skill_result intercepts IDLE_STALL before calling _compute_retry.
            # Explicit arm exists for exhaustiveness; unreachable in production.
            logger.debug("compute_retry_result", termination="IDLE_STALL", needs_retry=False)
            return False, RetryReason.NONE

        case TerminationReason.TIMED_OUT:
            # Wall-clock timeout: non-retriable (permanent infrastructure limit).
            logger.debug("compute_retry_result", termination="TIMED_OUT", needs_retry=False)
            return False, RetryReason.NONE

        case _ as unreachable:
            assert_never(unreachable)
