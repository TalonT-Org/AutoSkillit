"""Domain model for Claude Code headless session results.

L2 module: imports only from L0 (types, _logging). No server side-effects.
Centralizes all session-parsing concerns so callers can work with typed
objects instead of raw JSON strings.

Facade: re-exports from _session_model and _session_content sub-modules.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import assert_never

from autoskillit.core import (
    ChannelConfirmation,
    CliSubtype,
    RetryReason,
    SessionOutcome,
    SkillResult,
    TerminationReason,
    get_logger,
    truncate_text,
)
from autoskillit.execution._session_content import (
    _check_expected_patterns,
    _check_session_content,
    _evaluate_content_state,
)
from autoskillit.execution._session_model import (
    FAILURE_SUBTYPES,  # noqa: F401 — re-export for callers
    ClaudeSessionResult,
    ContentState,
    extract_token_usage,  # noqa: F401 — re-export for callers
    parse_session_result,  # noqa: F401 — re-export for callers
)

logger = get_logger(__name__)
_truncate = truncate_text
# Re-export SkillResult so existing callers can import from this module.
__all__ = ["CliSubtype", "SkillResult"]


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
