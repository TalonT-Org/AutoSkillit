"""Tests for _compute_retry, _is_kill_anomaly, and related retry adjudication logic."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
    TerminationReason,
)
from autoskillit.execution.session import (
    ClaudeSessionResult,
    ContentState,
    _compute_retry,
    _evaluate_content_state,
    _is_kill_anomaly,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestComputeRetry:
    """_compute_retry cross-validates all signals for retry eligibility."""

    def test_success_not_retriable(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        needs, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_max_turns_is_retriable(self):
        session = ClaudeSessionResult(
            subtype="error_max_turns", is_error=False, result="", session_id="s1"
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_context_exhaustion_is_retriable(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=True, result="Prompt is too long", session_id="s1"
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_thinking_stall_is_distinct_from_empty_output(self):
        """Thinking-only final turn produces THINKING_STALL, not EMPTY_OUTPUT.

        When the model's final turn contained only thinking blocks (has_thinking_only_turn=True)
        and the session has no text result and no tool calls, the retry reason must be
        THINKING_STALL rather than EMPTY_OUTPUT to allow callers to distinguish between
        "model produced nothing" and "model was actively reasoning."
        """
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            has_thinking_only_turn=True,
        )
        needs, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.THINKING_STALL

    def test_empty_output_without_thinking_flag_stays_empty_output(self):
        """EMPTY_OUTPUT retry reason requires has_thinking_only_turn=False."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            has_thinking_only_turn=False,
        )
        needs, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.EMPTY_OUTPUT

    def test_empty_output_exit_zero_is_retriable(self):
        """Infrastructure failure: session never ran, CLI exited cleanly.

        NATURAL_EXIT + rc=0 + empty_output subtype means the session process
        exited before writing any output — NOT a context exhaustion. The retry
        reason must be EMPTY_OUTPUT, not RESUME, because no partial progress
        exists on disk. RESUME is reserved for context/turn limit cases.
        """
        session = ClaudeSessionResult(
            subtype="empty_output", is_error=True, result="", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.EMPTY_OUTPUT

    def test_empty_output_exit_one_not_retriable(self):
        """Real failure: CLI crashed with empty output."""
        session = ClaudeSessionResult(
            subtype="empty_output", is_error=True, result="", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_timeout_not_retriable(self):
        session = ClaudeSessionResult(subtype="timeout", is_error=True, result="", session_id="")
        needs, reason = _compute_retry(
            session, returncode=-1, termination=TerminationReason.TIMED_OUT
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_unparseable_not_retriable(self):
        session = ClaudeSessionResult(
            subtype="unparseable", is_error=True, result="crash", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_execution_error_not_retriable(self):
        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="tool error",
            session_id="s1",
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_channel_b_completed_no_retry(self):
        """CHANNEL_B is authoritative on COMPLETED — no retry."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        needs, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_channel_a_completed_kill_anomaly_retries(self):
        """CHANNEL_A + empty result → retry RESUME (kill anomaly suspected)."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        needs, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_natural_exit_channel_b_no_retry(self):
        """CHANNEL_B confirmation skips kill-anomaly check on NATURAL_EXIT."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        needs, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        assert needs is False
        assert reason == RetryReason.NONE

    def test_natural_exit_channel_a_no_retry(self):
        """CHANNEL_A confirmation skips kill-anomaly check on NATURAL_EXIT."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        needs, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        assert needs is False
        assert reason == RetryReason.NONE


class TestComputeRetryUnparseable:
    """_compute_retry distinguishes unparseable under COMPLETED vs NATURAL_EXIT."""

    def test_unparseable_subtype_with_nonzero_returncode_should_retry(self):
        """unparseable under COMPLETED means process was killed mid-write.

        The drain timeout expired before the result record was fully flushed.
        The session likely completed; retry with resume.
        """
        session = ClaudeSessionResult(
            subtype="unparseable", is_error=True, result="partial", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        assert needs is True
        assert reason == RetryReason.RESUME

    def test_unparseable_subtype_natural_exit_no_retry(self):
        """unparseable under NATURAL_EXIT is a content failure, not retryable.

        The process exited cleanly with malformed output — this is not a timing issue.
        """
        session = ClaudeSessionResult(
            subtype="unparseable", is_error=True, result="", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=1, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is False
        assert reason == RetryReason.NONE


class TestIsKillAnomaly:
    """_is_kill_anomaly covers exactly the subtypes that represent kill artifacts."""

    @pytest.mark.parametrize(
        "subtype,result,expected",
        [
            ("unparseable", "", True),  # killed mid-write → partial NDJSON
            ("empty_output", "", True),  # killed before any stdout written
            ("success", "", True),  # killed after result record, content empty
            ("success", "x", False),  # success with content → NOT an anomaly
            ("error_during_execution", "", False),  # explicit API error, not a kill artifact
            ("timeout", "", False),  # timeout is a separate terminal state
        ],
    )
    def test_anomaly_classification(self, subtype: str, result: str, expected: bool) -> None:
        session = ClaudeSessionResult(
            subtype=subtype,
            is_error=(subtype != "success"),
            result=result,
            session_id="",
            errors=[],
            token_usage=None,
        )
        assert _is_kill_anomaly(session) is expected


class TestComputeRetrySuccessEmptyResult:
    """_compute_retry for success subtype with empty result under COMPLETED termination."""

    def test_success_empty_result_completed_rc0_is_retriable(self) -> None:
        """success + "" + COMPLETED + rc=0 must be retriable (drain-race glitch)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            errors=[],
            token_usage=None,
        )
        retriable, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.COMPLETED
        )
        assert retriable is True
        assert reason == RetryReason.RESUME

    def test_success_empty_result_completed_negative_rc_is_retriable(self) -> None:
        """success + "" + COMPLETED + rc=-15 (SIGTERM kill) must also be retriable."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            errors=[],
            token_usage=None,
        )
        retriable, reason = _compute_retry(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        assert retriable is True
        assert reason == RetryReason.RESUME

    def test_success_nonempty_result_completed_is_not_retriable(self) -> None:
        """success + non-empty result + COMPLETED must NOT be retriable (genuine success)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Done. %%ORDER_UP%%",
            session_id="s1",
            errors=[],
            token_usage=None,
        )
        retriable, _ = _compute_retry(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        assert retriable is False

    def test_success_empty_result_natural_exit_zero_rc_is_retriable(self) -> None:
        """Transient empty result: session exited cleanly but result field is empty.

        NATURAL_EXIT + rc=0 + SUCCESS subtype + empty result covers any case where
        the Claude process self-exited cleanly without writing output — including
        CLAUDE_CODE_EXIT_AFTER_STOP_DELAY timer-based exits AND transient API failures.
        This is NOT a context exhaustion (no jsonl marker, no error markers). The retry
        reason must be EMPTY_OUTPUT, not RESUME: no partial progress on disk.
        """
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            errors=[],
            token_usage=None,
        )
        retriable, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert retriable is True
        assert reason == RetryReason.EMPTY_OUTPUT

    def test_empty_output_completed_negative_rc_is_retriable(self) -> None:
        """empty_output + COMPLETED + rc=-15 must be retriable.

        Process was killed by infrastructure before writing any stdout.
        """
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            errors=[],
            token_usage=None,
        )
        retriable, reason = _compute_retry(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        assert retriable is True
        assert reason == RetryReason.RESUME

    def test_context_exhausted_natural_exit_zero_rc_emits_resume(self) -> None:
        """Context exhaustion on NATURAL_EXIT still emits RESUME.

        When _is_context_exhausted() returns True (jsonl_context_exhausted=True),
        NATURAL_EXIT + kill_anomaly must still emit RESUME — partial context
        progress exists on disk and is safe to resume.
        """
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="",
            jsonl_context_exhausted=True,
        )
        needs, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.RESUME  # context exhausted = partial progress exists

    def test_completed_kill_anomaly_still_emits_resume(self) -> None:
        """Infrastructure kill (COMPLETED) + kill anomaly → RESUME unchanged.

        COMPLETED termination means infrastructure killed the process after channel
        confirmation — the session made progress, partial results exist. RESUME
        is correct here regardless of _is_context_exhausted().
        """
        session = ClaudeSessionResult(subtype="success", is_error=False, result="", session_id="")
        needs, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        assert needs is True
        assert reason == RetryReason.RESUME  # infrastructure kill — progress existed


class TestComputeRetryCompletedPath:
    """_compute_retry unique test: COMPLETED termination + unparseable."""

    def test_unparseable_on_completed_returns_resume(self):
        s = ClaudeSessionResult(
            subtype="unparseable", is_error=True, result="garbled", session_id=""
        )
        needs, reason = _compute_retry(s, -15, TerminationReason.COMPLETED)
        assert needs is True
        assert reason == RetryReason.RESUME


@pytest.mark.parametrize("termination", list(TerminationReason))
def test_compute_retry_handles_all_termination_reasons_without_raising(
    termination: TerminationReason,
) -> None:
    """Exhaustiveness guard: _compute_retry must return valid (bool, RetryReason)
    for every TerminationReason value."""
    session = ClaudeSessionResult(
        subtype="success",
        result="done. %%ORDER_UP%%",
        is_error=False,
        session_id="abc",
        errors=[],
    )
    result = _compute_retry(session, returncode=0, termination=termination)
    assert isinstance(result, tuple) and len(result) == 2
    assert isinstance(result[0], bool)
    assert isinstance(result[1], RetryReason)


def test_is_kill_anomaly_returns_true_for_interrupted_subtype():
    """'interrupted' subtype under COMPLETED termination must be retriable."""
    session = ClaudeSessionResult(
        subtype="interrupted",
        result="",
        is_error=True,
        session_id="abc",
        errors=[],
    )
    assert _is_kill_anomaly(session) is True


class TestSessionCompleteProperty:
    """session_complete property must encode the canonical completion invariant."""

    @pytest.mark.parametrize(
        "subtype, is_error, expected",
        [
            ("success", False, True),
            ("error_max_turns", False, True),
            ("error_during_execution", False, True),
            ("context_exhaustion", False, True),
            ("interrupted", False, True),
            ("unknown", True, False),
            ("empty_output", True, False),
            ("unparseable", True, False),
            ("timeout", True, False),
        ],
    )
    def test_session_complete_property_values(
        self, subtype: str, is_error: bool, expected: bool
    ) -> None:
        """session_complete must be True iff not is_error AND subtype not in FAILURE_SUBTYPES."""
        session = ClaudeSessionResult(
            subtype=subtype,
            is_error=is_error,
            result="",
            session_id="test",
        )
        assert session.session_complete is expected


class TestContentStateEnum:
    """ContentState enum exposes the four expected variants."""

    def test_content_state_enum_variants(self) -> None:
        """ContentState exposes the four expected variants."""
        assert ContentState.COMPLETE == "complete"
        assert ContentState.ABSENT == "absent"
        assert ContentState.CONTRACT_VIOLATION == "contract_violation"
        assert ContentState.SESSION_ERROR == "session_error"

    @pytest.mark.parametrize(
        "result, is_error, completion_marker, patterns, expected_state",
        [
            # COMPLETE: result present, marker present, patterns match
            (
                "verdict = GO\n%%ORDER_UP%%",
                False,
                "%%ORDER_UP%%",
                ["verdict\\s*=\\s*(GO|NO GO)"],
                "complete",
            ),
            # ABSENT: empty result
            ("", False, "%%ORDER_UP%%", ["verdict\\s*=\\s*(GO|NO GO)"], "absent"),
            # ABSENT: marker missing from non-empty result
            ("Some output, no marker", False, "%%ORDER_UP%%", [], "absent"),
            # CONTRACT_VIOLATION: result + marker present, patterns fail
            (
                "Done. %%ORDER_UP%%",
                False,
                "%%ORDER_UP%%",
                ["verdict\\s*=\\s*(GO|NO GO)"],
                "contract_violation",
            ),
            # SESSION_ERROR: is_error=True
            ("Error: max turns exceeded", True, "%%ORDER_UP%%", [], "session_error"),
        ],
    )
    def test_evaluate_content_state(
        self,
        result: str,
        is_error: bool,
        completion_marker: str,
        patterns: list[str],
        expected_state: str,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        session = make_session(subtype="success", is_error=is_error, result=result)
        state = _evaluate_content_state(session, completion_marker, patterns)
        assert state.value == expected_state
