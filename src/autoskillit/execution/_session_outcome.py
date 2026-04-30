"""Session outcome adjudication sub-module."""

from __future__ import annotations

from collections.abc import Sequence
from typing import assert_never

from autoskillit.core import (
    ChannelConfirmation,
    CliSubtype,
    RetryReason,
    SessionOutcome,
    TerminationReason,
    get_logger,
)
from autoskillit.execution._retry_fsm import _compute_retry, _is_kill_anomaly
from autoskillit.execution._session_content import (
    _check_expected_patterns,
    _check_session_content,
    _evaluate_content_state,
)
from autoskillit.execution._session_model import ClaudeSessionResult, ContentState

logger = get_logger(__name__)

__all__ = ["_compute_success", "_compute_outcome"]


def _compute_success(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
    completion_marker: str = "",
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
    expected_output_patterns: Sequence[str] = (),
) -> bool:
    """Cross-validate all signals to determine unambiguous success/failure.

    Exhaustive match dispatch over TerminationReason ensures mypy flags any
    unhandled value when the enum is extended (ARCH-007).

    Gate 0.5 (provenance bypass): when ``channel_confirmation=CHANNEL_B``,
    Channel B's session-JSONL marker is the authoritative signal that the
    session completed successfully. Stdout content is not required.
    """
    # Gate 0.5: Channel B provenance bypass — session JSONL is authoritative.
    match channel_confirmation:
        case ChannelConfirmation.CHANNEL_B:
            if expected_output_patterns and not session.session_complete:
                logger.debug(
                    "channel_b_bypass_skipped_incomplete_session",
                    subtype=str(session.subtype),
                    is_error=session.is_error,
                    pattern_count=len(expected_output_patterns),
                )
            else:
                if not _check_expected_patterns(session.result.strip(), expected_output_patterns):
                    logger.debug(
                        "channel_b_content_check_failed",
                        result_len=len(session.result),
                        pattern_count=len(expected_output_patterns),
                    )
                    return False
                logger.debug("compute_success_bypass", channel="CHANNEL_B", result=True)
                return True
        case (
            ChannelConfirmation.CHANNEL_A
            | ChannelConfirmation.UNMONITORED
            | ChannelConfirmation.DIR_MISSING
        ):
            pass  # fall through to termination dispatch
        case _ as _unreachable_cc:
            assert_never(_unreachable_cc)

    match termination:
        case TerminationReason.TIMED_OUT:
            return False

        case TerminationReason.STALE:
            return False

        case TerminationReason.IDLE_STALL:
            return False

        case TerminationReason.COMPLETED:
            # The process was killed by our own async_kill_process_tree
            # (signal -15 or -9), so a non-zero returncode is expected and
            # trustworthy when the session envelope says "success".
            if returncode != 0 and not (
                session.subtype == CliSubtype.SUCCESS and session.result.strip()
            ):
                return False
            content_ok = _check_session_content(
                session, completion_marker, expected_output_patterns
            )
            logger.debug(
                "compute_success_termination",
                termination="COMPLETED",
                returncode=returncode,
                content_check=content_ok,
            )
            return content_ok

        case TerminationReason.NATURAL_EXIT:
            # The process exited on its own. A non-zero returncode is normally
            # authoritative evidence of failure — no asymmetric bypass.
            #
            # Post-completion kill bypass: when an external watchdog kills the
            # process AFTER it finished its work (e.g. trailing async task cleanup),
            # the signal is a teardown artifact. The completion marker in the result
            # provides strong evidence of completion — trust it over the returncode.
            if returncode != 0:
                if (
                    session.subtype == CliSubtype.SUCCESS
                    and session.result.strip()
                    and completion_marker
                    and completion_marker in session.result
                ):
                    content_ok = _check_session_content(
                        session, completion_marker, expected_output_patterns
                    )
                    logger.debug(
                        "compute_success_natural_exit_post_completion_kill",
                        returncode=returncode,
                        content_check=content_ok,
                    )
                    return content_ok
                return False
            content_ok = _check_session_content(
                session, completion_marker, expected_output_patterns
            )
            logger.debug(
                "compute_success_termination",
                termination="NATURAL_EXIT",
                returncode=returncode,
                content_check=content_ok,
            )
            return content_ok

        case _ as unreachable:
            assert_never(unreachable)


def _compute_outcome(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
    completion_marker: str = "",
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
    expected_output_patterns: Sequence[str] = (),
) -> tuple[SessionOutcome, RetryReason]:
    """Compose _compute_success and _compute_retry into a (SessionOutcome, RetryReason) pair.

    Applies contradiction guard (retry demotes success) and dead-end guard (ABSENT
    drain-race promotes to RETRIABLE) before mapping to the bijective SessionOutcome enum.
    """
    success = _compute_success(
        session,
        returncode,
        termination,
        completion_marker,
        channel_confirmation,
        expected_output_patterns,
    )
    needs_retry, retry_reason = _compute_retry(
        session, returncode, termination, channel_confirmation, completion_marker
    )

    logger.debug(
        "compute_outcome_inputs",
        success=success,
        needs_retry=needs_retry,
        retry_reason=str(retry_reason),
        returncode=returncode,
        termination=str(termination),
        channel=str(channel_confirmation),
        subtype=session.subtype,
        is_error=session.is_error,
        result_empty=not session.result.strip(),
    )

    # Contradiction guard: retry signal is authoritative over the channel bypass.
    if success and needs_retry:
        success = False
        logger.debug(
            "contradiction_guard",
            action="demoted_success",
            reason="retry_signal_authoritative",
        )

    # Dead-end guard: channel confirmation means the session signalled completion, but
    # content checks failed and no retry was scheduled by _compute_retry.
    # Only promote to RETRIABLE when the failure is a drain-race artifact (ABSENT state):
    #   - empty result → stdout not fully flushed
    #   - marker missing → partial flush
    # Do NOT promote CONTRACT_VIOLATION or SESSION_ERROR — these are terminal failures
    # that retrying will never resolve.
    if not success and not needs_retry:
        match channel_confirmation:
            case ChannelConfirmation.CHANNEL_A | ChannelConfirmation.CHANNEL_B:
                content_state = _evaluate_content_state(
                    session, completion_marker, expected_output_patterns
                )
                if content_state == ContentState.ABSENT:
                    needs_retry = True
                    retry_reason = RetryReason.DRAIN_RACE
                    logger.debug(
                        "dead_end_guard",
                        action="promoted_to_retriable",
                        content_state=content_state.value,
                        channel=channel_confirmation.value,
                    )
                else:
                    logger.debug(
                        "dead_end_guard",
                        action="terminal_failure_not_promoted",
                        content_state=content_state.value,
                        channel=channel_confirmation.value,
                    )
            case ChannelConfirmation.UNMONITORED | ChannelConfirmation.DIR_MISSING:
                pass  # legitimate terminal failure — no channel confirmed completion
            case _ as unreachable_cc:
                assert_never(unreachable_cc)

    if success:
        outcome = SessionOutcome.SUCCEEDED
    elif needs_retry:
        outcome = SessionOutcome.RETRIABLE
    else:
        outcome = SessionOutcome.FAILED

    logger.debug(
        "compute_outcome_result",
        outcome=str(outcome),
        retry_reason=str(retry_reason),
    )
    return outcome, retry_reason
