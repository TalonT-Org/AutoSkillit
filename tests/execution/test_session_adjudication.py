"""Tests for _compute_success, _compute_retry, _is_kill_anomaly, and adjudication consistency."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
    SessionOutcome,
    TerminationReason,
)
from autoskillit.execution.session import (
    ClaudeSessionResult,
    ContentState,
    _check_expected_patterns,
    _check_session_content,
    _compute_outcome,
    _compute_retry,
    _compute_success,
    _evaluate_content_state,
    _is_kill_anomaly,
    parse_session_result,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_success_session(result: str = "done") -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype="success",
        is_error=False,
        result=result,
        session_id="s1",
    )


@pytest.fixture
def make_session() -> Callable[..., ClaudeSessionResult]:
    def _factory(
        subtype: str = "success",
        is_error: bool = False,
        result: str = "",
        assistant_messages: list[str] | None = None,
    ) -> ClaudeSessionResult:
        return ClaudeSessionResult(
            subtype=subtype,
            is_error=is_error,
            result=result,
            session_id="test-session",
            assistant_messages=assistant_messages or [],
        )

    return _factory


class TestComputeSuccess:
    """_compute_success cross-validates all signals for unambiguous success."""

    def test_all_good_is_success(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.NATURAL_EXIT)
            is True
        )

    def test_nonzero_exit_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=1, termination=TerminationReason.NATURAL_EXIT)
            is False
        )

    def test_is_error_true_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=True, result="Error occurred", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.NATURAL_EXIT)
            is False
        )

    def test_timed_out_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.TIMED_OUT)
            is False
        )

    def test_stale_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.STALE) is False
        )

    def test_unknown_subtype_is_failure(self):
        session = ClaudeSessionResult(
            subtype="unknown", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.NATURAL_EXIT)
            is False
        )

    def test_channel_b_bypasses_content_check(self):
        """CHANNEL_B + COMPLETED + empty result → True (provenance bypass)."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                channel_confirmation=ChannelConfirmation.CHANNEL_B,
            )
            is True
        )

    def test_channel_a_falls_through_to_content_check(self):
        """CHANNEL_A + COMPLETED + empty result → False (content check required)."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.COMPLETED,
                channel_confirmation=ChannelConfirmation.CHANNEL_A,
            )
            is False
        )

    def test_unmonitored_falls_through_to_content_check(self):
        """UNMONITORED delegates to normal gates."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                channel_confirmation=ChannelConfirmation.UNMONITORED,
            )
            is False
        )

    def test_natural_exit_channel_b_empty_result_true(self):
        """NATURAL_EXIT + CHANNEL_B bypass fires before termination dispatch."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                channel_confirmation=ChannelConfirmation.CHANNEL_B,
            )
            is True
        )

    def test_natural_exit_channel_a_valid_result_true(self):
        """NATURAL_EXIT + CHANNEL_A + valid content → True."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert (
            _compute_success(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                channel_confirmation=ChannelConfirmation.CHANNEL_A,
            )
            is True
        )


class TestComputeSuccessNaturalExitNonZero:
    """NATURAL_EXIT with non-zero returncode.

    Default: failure (no bypass). Exception: post-completion kill bypass fires when
    the session envelope is SUCCESS, result is non-empty, and the completion marker
    is present — the signal is a teardown artifact, not a genuine CLI error.
    """

    def test_natural_exit_nonzero_returncode_without_marker_returns_false(self):
        """NATURAL_EXIT + non-zero returncode + no completion marker → failure.

        Without a completion marker, we cannot distinguish a post-completion kill
        from a genuine crash. Fail conservatively (PTY masking cannot be ruled out).
        """
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="done", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=1, termination=TerminationReason.NATURAL_EXIT)
            is False
        )

    def test_completed_and_natural_exit_same_outcome_when_returncode_zero(self):
        """COMPLETED and NATURAL_EXIT agree when returncode=0 (no PTY masking issue).

        Documents the symmetric case: asymmetry only matters when returncode != 0.
        """
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="done", session_id="s1"
        )
        result_completed = _compute_success(
            session, returncode=-15, termination=TerminationReason.COMPLETED
        )
        result_natural = _compute_success(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert result_completed is True
        assert result_natural is True


class TestComputeSuccessNaturalExitPostCompletionKill:
    """NATURAL_EXIT with non-zero returncode is success when completion marker is present."""

    def test_natural_exit_sigkill_with_marker_is_success(self):
        """NATURAL_EXIT + returncode -9 + SUCCESS subtype + completion marker → success.

        Documents the post-completion kill bypass: a process killed by an external
        watchdog AFTER completing its work produces -9 on NATURAL_EXIT. The completion
        marker in the result confirms the session finished before the kill.
        """
        marker = "%%ORDER_UP%%"
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"fixes_applied = 2\n{marker}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-9,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=marker,
            )
            is True
        )

    def test_natural_exit_sigkill_without_marker_remains_failure(self):
        """No bypass when no completion_marker is configured — conservative path unchanged."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="fixes_applied = 2",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-9,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker="",  # no marker configured
            )
            is False
        )

    def test_natural_exit_sigkill_marker_absent_from_result_remains_failure(self):
        """Bypass does not fire when marker is configured but missing from result."""
        marker = "%%ORDER_UP%%"
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="partial output without marker",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-9,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=marker,
            )
            is False
        )

    def test_natural_exit_nonzero_non_success_subtype_remains_failure(self):
        """Bypass does not fire for non-success subtypes even with marker present."""
        marker = "%%ORDER_UP%%"
        session = ClaudeSessionResult(
            subtype="unparseable",
            is_error=False,
            result=f"some output {marker}",
            session_id="s1",
        )
        assert (
            _compute_success(
                session,
                returncode=-9,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker=marker,
            )
            is False
        )


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


class TestComputeSuccessCompletionMarker:
    """_compute_success tests unique to test_session_result.py (completion_marker path)."""

    def test_nonzero_returncode_recoverable_path(self):
        s = ClaudeSessionResult(
            subtype="success", is_error=False, result="great output", session_id="s1"
        )
        assert _compute_success(s, 1, TerminationReason.COMPLETED) is True

    def test_failure_subtype_false_for_more_subtypes(self):
        for subtype in ("empty_output", "unparseable", "timeout"):
            s = ClaudeSessionResult(
                subtype=subtype, is_error=False, result="some text", session_id="s1"
            )
            assert _compute_success(s, 0, TerminationReason.NATURAL_EXIT) is False

    def test_missing_completion_marker_false(self):
        s = _make_success_session("result without marker")
        assert (
            _compute_success(s, 0, TerminationReason.NATURAL_EXIT, completion_marker="%%DONE%%")
            is False
        )

    def test_result_is_only_marker_false(self):
        marker = "%%DONE%%"
        s = _make_success_session(marker)
        assert (
            _compute_success(s, 0, TerminationReason.NATURAL_EXIT, completion_marker=marker)
            is False
        )


class TestComputeSuccessRealisticInputs:
    """_compute_success using parse_session_result() as input constructor."""

    def test_empty_stdout_parses_to_empty_output_adjudicates_false(self):
        session = parse_session_result("")
        assert session.subtype == "empty_output"
        assert session.is_error is True
        assert _compute_success(session, 0, TerminationReason.NATURAL_EXIT) is False

    def test_garbled_stdout_parses_to_unparseable_adjudicates_false(self):
        session = parse_session_result("Traceback (most recent call last):\n  boom\n")
        assert session.subtype == "unparseable"
        assert session.is_error is True
        assert _compute_success(session, 0, TerminationReason.NATURAL_EXIT) is False

    def test_empty_stdout_not_bypassed_by_completed_path(self):
        session = parse_session_result("")
        assert _compute_success(session, -15, TerminationReason.COMPLETED) is False

    def test_unparseable_not_bypassed_by_completed_path(self):
        session = parse_session_result("garbled output not json\n")
        assert _compute_success(session, -15, TerminationReason.COMPLETED) is False


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


@pytest.mark.parametrize("termination", list(TerminationReason))
def test_compute_success_handles_all_termination_reasons_without_raising(
    termination: TerminationReason,
) -> None:
    """Exhaustiveness guard for _compute_success: must return bool for every TerminationReason."""
    session = ClaudeSessionResult(
        subtype="success",
        result="done. %%ORDER_UP%%",
        is_error=False,
        session_id="abc",
        errors=[],
    )
    result = _compute_success(
        session,
        returncode=0,
        termination=termination,
        completion_marker="%%ORDER_UP%%",
    )
    assert isinstance(result, bool)


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


class TestAdjudicationConsistency:
    """Contract documentation for known-impossible adjudication states.

    These tests intentionally cover (returncode, termination) combinations that
    cannot occur in production. They serve as specification — documenting that the
    adjudicator is deterministic and exhaustive, not that these paths are reachable.
    """

    @pytest.mark.parametrize(
        "termination,channel,result_content,returncode,subtype,is_error,"
        "expected_success,expected_retry,completion_marker",
        [
            # NATURAL_EXIT + CHANNEL_A: dead-end state
            # (known bug, corrected by guard in _build_skill_result)
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_A,
                "",
                0,
                "success",
                False,
                False,
                False,
                "",
            ),
            # NATURAL_EXIT + CHANNEL_A: valid success with content
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_A,
                "done",
                0,
                "success",
                False,
                True,
                False,
                "",
            ),
            # NATURAL_EXIT + CHANNEL_B: contradiction
            # (known bug, corrected by guard in _build_skill_result)
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_B,
                "",
                0,
                "error_max_turns",
                True,
                True,
                True,
                "",
            ),
            # NATURAL_EXIT + CHANNEL_B: valid success
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_B,
                "",
                0,
                "success",
                False,
                True,
                False,
                "",
            ),
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_B,
                "done",
                0,
                "success",
                False,
                True,
                False,
                "",
            ),
            # COMPLETED + CHANNEL_A: valid retriable (kill anomaly)
            (
                TerminationReason.COMPLETED,
                ChannelConfirmation.CHANNEL_A,
                "",
                -15,
                "success",
                False,
                False,
                True,
                "",
            ),
            # COMPLETED + CHANNEL_B: valid success
            (
                TerminationReason.COMPLETED,
                ChannelConfirmation.CHANNEL_B,
                "",
                -15,
                "success",
                False,
                True,
                False,
                "",
            ),
            # COMPLETED + CHANNEL_B: contradiction
            # (known bug, corrected by guard in _build_skill_result)
            (
                TerminationReason.COMPLETED,
                ChannelConfirmation.CHANNEL_B,
                "",
                -15,
                "error_max_turns",
                True,
                True,
                True,
                "",
            ),
            # UNMONITORED baselines — all should already be valid states
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.UNMONITORED,
                "",
                0,
                "success",
                False,
                False,
                True,
                "",
            ),
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.UNMONITORED,
                "done",
                0,
                "success",
                False,
                True,
                False,
                "",
            ),
            (
                TerminationReason.TIMED_OUT,
                ChannelConfirmation.UNMONITORED,
                "",
                -1,
                "timeout",
                True,
                False,
                False,
                "",
            ),
            (
                TerminationReason.STALE,
                ChannelConfirmation.UNMONITORED,
                "",
                0,
                "success",
                False,
                False,
                False,
                "",
            ),
            # NATURAL_EXIT + UNMONITORED: substantive result without marker
            # (premature exit / early stop — retriable via EARLY_STOP)
            pytest.param(
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.UNMONITORED,
                "Here is the PR context block with substantive content...",
                0,
                "success",
                False,
                False,
                True,
                "%%ORDER_UP%%",
                id="natural_exit-unmonitored-substantive_result_no_marker",
            ),
        ],
    )
    def test_raw_adjudication_pair(
        self,
        termination: TerminationReason,
        channel: ChannelConfirmation,
        result_content: str,
        returncode: int,
        subtype: str,
        is_error: bool,
        expected_success: bool,
        expected_retry: bool,
        completion_marker: str,
    ) -> None:
        """Document exact raw outputs of the individual adjudication functions.

        Known bad states (dead end, contradiction) are documented as expected values
        rather than as invariant failures — the guards in _build_skill_result correct
        those states before they reach the orchestrator.
        """
        session = ClaudeSessionResult(
            subtype=subtype,
            result=result_content,
            is_error=is_error,
            session_id="cross-val",
            errors=[],
        )
        success = _compute_success(
            session,
            returncode,
            termination,
            channel_confirmation=channel,
            completion_marker=completion_marker,
        )
        needs_retry, _ = _compute_retry(
            session,
            returncode,
            termination,
            channel_confirmation=channel,
            completion_marker=completion_marker,
        )
        assert success == expected_success
        assert needs_retry == expected_retry

    def test_premature_exit_substantive_result_no_marker_is_early_stop(self) -> None:
        """NATURAL_EXIT + UNMONITORED + substantive result without marker → EARLY_STOP.

        When the model produces substantive output but stops before emitting
        the completion marker, the session is classified as retriable with
        EARLY_STOP reason. This is the text-then-tool boundary fix.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="Here is the PR context block with substantive content...",
            is_error=False,
            session_id="premature",
            errors=[],
        )
        success = _compute_success(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        assert success is False
        assert needs_retry is True
        assert reason == RetryReason.EARLY_STOP

    def test_channel_a_empty_result_raw_dead_end(self) -> None:
        """Document: NATURAL_EXIT + CHANNEL_A + empty result is a dead end at raw function level.

        Both _compute_success and _compute_retry return False for this combination:
        - _compute_success: CHANNEL_A falls through content check, empty result → False
        - _compute_retry: NATURAL_EXIT + CHANNEL_A confirmation suppresses retry → False

        The composition guard in _build_skill_result escalates this to retriable.
        See TestAdjudicationGuards.test_channel_a_empty_result_not_dead_end.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="",
            is_error=False,
            session_id="regression",
            errors=[],
        )
        success = _compute_success(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        needs_retry, _ = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        # Raw functions produce the dead-end state — this is the known bug at the
        # individual function level, corrected at the _build_skill_result boundary.
        assert success is False
        assert needs_retry is False


class TestEarlyStop:
    """Tests for EARLY_STOP retry classification."""

    def test_natural_exit_substantive_content_no_marker_is_retriable(self) -> None:
        """A session that exits cleanly with substantive output but without
        the completion marker should be classified as RETRIABLE with EARLY_STOP,
        because the model may have stopped early at a text-then-tool boundary.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="Here is the PR context block with substantive content...",
            is_error=False,
            session_id="early-stop",
            errors=[],
        )
        outcome, reason = _compute_outcome(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        assert outcome == SessionOutcome.RETRIABLE
        assert reason == RetryReason.EARLY_STOP

    def test_early_stop_not_triggered_without_marker(self) -> None:
        """When no completion_marker is configured, EARLY_STOP should NOT fire.

        Sessions without a marker are not subject to early-stop detection.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="Some output without any marker",
            is_error=False,
            session_id="no-marker",
            errors=[],
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="",
        )
        assert needs_retry is False
        assert reason == RetryReason.NONE

    def test_early_stop_not_triggered_when_marker_present(self) -> None:
        """When the completion marker IS present, EARLY_STOP should NOT fire."""
        session = ClaudeSessionResult(
            subtype="success",
            result="Result with %%ORDER_UP%% marker present",
            is_error=False,
            session_id="has-marker",
            errors=[],
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        assert needs_retry is False
        assert reason == RetryReason.NONE

    def test_early_stop_not_triggered_for_errors(self) -> None:
        """Error sessions should not be classified as EARLY_STOP."""
        session = ClaudeSessionResult(
            subtype="error_during_execution",
            result="Error occurred but substantive output...",
            is_error=True,
            session_id="error-session",
            errors=["something broke"],
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        # is_error sessions may trigger API-level retry via needs_retry property,
        # but EARLY_STOP specifically should not fire for non-success subtypes
        assert reason != RetryReason.EARLY_STOP

    def test_early_stop_not_triggered_for_empty_result(self) -> None:
        """Empty result should be classified as kill anomaly, not EARLY_STOP."""
        session = ClaudeSessionResult(
            subtype="success",
            result="",
            is_error=False,
            session_id="empty",
            errors=[],
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        # Empty result triggers kill_anomaly path (EMPTY_OUTPUT), not EARLY_STOP.
        # No context exhaustion detected → EMPTY_OUTPUT, not RESUME.
        assert needs_retry is True
        assert reason == RetryReason.EMPTY_OUTPUT


class TestArtifactValidation:
    """Tests for expected_output_patterns artifact validation."""

    def test_check_session_content_validates_expected_artifacts(self) -> None:
        """When expected_output_patterns are configured, _check_session_content
        must verify that the session result contains at least one match.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="Done! %%ORDER_UP%%",
            is_error=False,
            session_id="no-artifact",
            errors=[],
        )
        # With marker present but no matching artifact
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[r"https://github\.com/.*/pull/\d+"],
        )
        assert result is False

    def test_check_session_content_passes_with_matching_artifact(self) -> None:
        """When artifacts match, content check passes."""
        session = ClaudeSessionResult(
            subtype="success",
            result="PR created: https://github.com/user/repo/pull/42 %%ORDER_UP%%",
            is_error=False,
            session_id="has-artifact",
            errors=[],
        )
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[r"https://github\.com/.*/pull/\d+"],
        )
        assert result is True

    def test_check_session_content_no_patterns_skips_validation(self) -> None:
        """When no patterns are provided, artifact validation is skipped."""
        session = ClaudeSessionResult(
            subtype="success",
            result="Done! %%ORDER_UP%%",
            is_error=False,
            session_id="no-patterns",
            errors=[],
        )
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[],
        )
        assert result is True

    def test_compute_outcome_threads_expected_output_patterns(self) -> None:
        """_compute_outcome must thread expected_output_patterns through to
        _compute_success and _check_session_content."""
        session = ClaudeSessionResult(
            subtype="success",
            result="Done! %%ORDER_UP%%",
            is_error=False,
            session_id="threaded",
            errors=[],
        )
        # Without patterns: success
        outcome_no_patterns, _ = _compute_outcome(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            expected_output_patterns=[],
        )
        assert outcome_no_patterns == SessionOutcome.SUCCEEDED

        # With patterns that don't match: failed (EARLY_STOP since marker
        # absent from _compute_success perspective when artifact check fails)
        outcome_with_patterns, _ = _compute_outcome(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            expected_output_patterns=[r"https://github\.com/.*/pull/\d+"],
        )
        assert outcome_with_patterns != SessionOutcome.SUCCEEDED


class TestToolUseParsing:
    """Tests for tool_use NDJSON record extraction."""

    @staticmethod
    def _result_line() -> str:
        import json

        return json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
            }
        )

    @staticmethod
    def _assistant_line(*content_blocks: dict) -> str:  # type: ignore[type-arg]
        import json

        return json.dumps(
            {
                "type": "assistant",
                "message": {"content": list(content_blocks)},
            }
        )

    def test_parse_session_result_captures_tool_uses(self) -> None:
        """parse_session_result must extract tool_use records."""
        ndjson = "\n".join(
            [
                self._assistant_line(
                    {"type": "tool_use", "name": "Skill", "id": "tu_1"},
                    {"type": "text", "text": "loading skill"},
                ),
                self._result_line(),
            ]
        )
        session = parse_session_result(ndjson)
        assert len(session.tool_uses) == 1
        assert session.tool_uses[0]["name"] == "Skill"
        assert session.tool_uses[0]["id"] == "tu_1"

    def test_parse_session_result_no_tool_uses(self) -> None:
        """Sessions without tool_use records have an empty list."""
        ndjson = "\n".join(
            [
                self._assistant_line(
                    {"type": "text", "text": "just text"},
                ),
                self._result_line(),
            ]
        )
        session = parse_session_result(ndjson)
        assert session.tool_uses == []

    def test_parse_session_result_multiple_tool_uses(self) -> None:
        """Multiple tool_use records across messages are captured."""
        ndjson = "\n".join(
            [
                self._assistant_line(
                    {"type": "tool_use", "name": "Write", "id": "tu_1"},
                ),
                self._assistant_line(
                    {"type": "tool_use", "name": "Skill", "id": "tu_2"},
                ),
                self._result_line(),
            ]
        )
        session = parse_session_result(ndjson)
        assert len(session.tool_uses) == 2
        assert session.tool_uses[0]["name"] == "Write"
        assert session.tool_uses[1]["name"] == "Skill"


class TestCheckExpectedPatterns:
    """Unit tests for the standalone _check_expected_patterns function."""

    def test_check_expected_patterns_present(self) -> None:
        assert (
            _check_expected_patterns(
                result="some text ---my-block--- more text",
                patterns=["---my-block---"],
            )
            is True
        )

    def test_check_expected_patterns_absent(self) -> None:
        assert (
            _check_expected_patterns(
                result="some text without the block",
                patterns=["---my-block---"],
            )
            is False
        )

    def test_check_expected_patterns_empty_patterns_always_true(self) -> None:
        assert _check_expected_patterns(result="anything", patterns=[]) is True

    def test_check_expected_patterns_bold_wrapped_token_matches(self) -> None:
        """Bold-wrapped token name must match after normalization."""
        result = "**plan_path** = /abs/path/plan.md\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["plan_path\\s*=\\s*/.+"]) is True

    def test_check_expected_patterns_italic_wrapped_token_matches(self) -> None:
        """Italic-wrapped token name must match after normalization."""
        result = "*plan_path* = /abs/path/plan.md\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["plan_path\\s*=\\s*/.+"]) is True

    def test_check_expected_patterns_bold_verdict_matches(self) -> None:
        """Bold-wrapped verdict token must match after normalization."""
        result = "**verdict** = GO\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["verdict\\s*=\\s*(GO|NO GO)"]) is True

    def test_check_expected_patterns_multiple_bold_tokens_all_match(self) -> None:
        """Multiple bold-wrapped tokens must all match (AND semantics preserved)."""
        result = (
            "**plan_path** = /abs/path/plan.md\n**plan_parts** = /abs/path/plan.md\n%%ORDER_UP%%"
        )
        assert (
            _check_expected_patterns(result, ["plan_path\\s*=\\s*/.+", "plan_parts\\s*=\\s*/.+"])
            is True
        )

    def test_check_expected_patterns_bold_relative_path_still_fails(self) -> None:
        """Bold wrapping on a relative path must still fail — normalization must not
        mask a genuine contract violation (wrong value type)."""
        result = "**worktree_path** = ../worktrees/impl\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["worktree_path\\s*=\\s*/.+"]) is False

    def test_check_expected_patterns_bold_absent_value_still_fails(self) -> None:
        """Bold key with no value must still fail — semantic content must be present."""
        result = "**plan_path** =\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["plan_path\\s*=\\s*/.+"]) is False


class TestComputeSuccessChannelBPatterns:
    """Channel B bypass must not skip expected_output_patterns validation."""

    def test_compute_success_channel_b_bypass_with_missing_patterns_returns_false(
        self,
    ) -> None:
        """Channel B bypass must not skip expected_output_patterns validation.

        When patterns are configured and absent from session.result, success must
        be False (not True) even when channel_confirmation is CHANNEL_B.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="Final message without the block.",  # pattern absent
            is_error=False,
            session_id="s1",
            assistant_messages=["Final message without the block."],
        )
        success = _compute_success(
            session=session,
            returncode=0,
            termination=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=["---prepare-issue-result---"],
        )
        assert success is False, "Channel B bypass must not skip expected_output_patterns check"

    def test_channel_b_bypass_fails_for_failure_subtype_with_injected_pattern(
        self,
    ) -> None:
        """CHANNEL_B bypass must not return True for failure-subtype sessions.

        Even when synthesis has already run and injected a matching line into
        session.result, CHANNEL_B bypass must refuse because session.session_complete
        is False.
        """
        from autoskillit.core.types import CliSubtype

        # Simulate: synthesis already ran and injected the line into result
        session = ClaudeSessionResult(
            subtype=CliSubtype.UNPARSEABLE,
            is_error=True,
            result="plan_path = /cwd/.autoskillit/temp/make-plan/arch_lens_2026.md\n<raw JSONL>",
            session_id="s1",
        )
        success = _compute_success(
            session=session,
            returncode=0,
            termination=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=[r"plan_path\s*=\s*/.+"],
        )
        assert success is False, "CHANNEL_B bypass must not accept failure-subtype sessions"


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


class TestDeadEndGuardContentState:
    """Dead-end guard must distinguish drain-race artifacts from terminal failures."""

    def test_compute_outcome_channel_b_pattern_contract_violation_is_terminal(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Dead-end guard must NOT promote to RETRIABLE when session has content + marker
        but expected_output_patterns are absent — contract violation, not a drain race."""
        session = make_session(
            subtype="success",
            is_error=False,
            result="Investigation complete. %%ORDER_UP%%",
            assistant_messages=[],  # no assistant_messages to recover from
        )
        outcome, retry_reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["investigation_path\\s*=\\s*/.+"],
        )
        # Contract violation: result is non-empty, marker is present, but patterns are absent.
        # The dead-end guard must NOT promote this to RETRIABLE — retrying will never help.
        assert outcome == SessionOutcome.FAILED, (
            f"Expected FAILED for pattern contract violation, got {outcome}. "
            "Dead-end guard is incorrectly treating contract violations as drain-race artifacts."
        )
        assert retry_reason == RetryReason.NONE

    def test_compute_outcome_completed_channel_b_pattern_contract_violation_is_terminal(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Same contract-violation guard check for COMPLETED + CHANNEL_B termination path."""
        session = make_session(
            subtype="success",
            is_error=False,
            result="Done. %%ORDER_UP%%",
            assistant_messages=[],
        )
        outcome, retry_reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.COMPLETED,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["verdict\\s*=\\s*(GO|NO GO)"],
        )
        assert outcome == SessionOutcome.FAILED
        assert retry_reason == RetryReason.NONE

    def test_compute_outcome_channel_b_empty_result_is_still_retriable(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Regression test: drain-race rescue (empty result) must still be promoted to RETRIABLE.
        The ContentState fix must NOT break the existing drain-race handling."""
        session = make_session(
            subtype="success",
            is_error=False,
            result="",  # empty — drain race candidate
            assistant_messages=[],
        )
        outcome, retry_reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["investigation_path\\s*=\\s*/.+"],
        )
        assert outcome == SessionOutcome.RETRIABLE, (
            "Empty result with channel confirmation must remain RETRIABLE (drain-race rescue)."
        )
        assert retry_reason == RetryReason.DRAIN_RACE

    def test_compute_outcome_channel_b_missing_marker_is_still_retriable(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Regression: result present but marker absent is still RETRIABLE (partial drain)."""
        session = make_session(
            subtype="success",
            is_error=False,
            result="Some output without the marker",
            assistant_messages=[],
        )
        outcome, retry_reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["investigation_path\\s*=\\s*/.+"],
        )
        assert outcome == SessionOutcome.RETRIABLE, (
            "Missing completion marker with channel confirmation must remain RETRIABLE."
        )
        assert retry_reason == RetryReason.DRAIN_RACE

    def test_dead_end_guard_channel_confirmed_absent_emits_drain_race(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Channel confirmed + content ABSENT → DRAIN_RACE, not RESUME.

        DRAIN_RACE distinguishes "infrastructure confirmed completion, stdout not
        fully flushed" from "session hit context limit." Both route to on_context_limit
        because progress was confirmed, but the provenance is now explicit.
        """
        session = make_session(
            subtype="success",
            is_error=False,
            result="",  # empty — drain race candidate
            assistant_messages=[],
        )
        outcome, reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
            expected_output_patterns=(),
        )
        assert outcome == SessionOutcome.RETRIABLE
        assert reason == RetryReason.DRAIN_RACE  # not RESUME

    def test_compute_outcome_bold_wrapped_token_is_success_not_violation(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """A session with bold-wrapped structured output tokens must succeed,
        not be classified as CONTRACT_VIOLATION and returned as adjudicated_failure."""
        session = make_session(result="**plan_path** = /abs/path/plan.md\n%%ORDER_UP%%")
        outcome, reason = _compute_outcome(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["plan_path\\s*=\\s*/.+"],
        )
        assert outcome == SessionOutcome.SUCCEEDED
        assert reason == RetryReason.NONE


# ---------------------------------------------------------------------------
# T1: parse_session_result preserves file_path from Write/Edit tool_use input
# ---------------------------------------------------------------------------

import json  # noqa: E402 — imported here to keep T1 tests self-contained


@pytest.fixture
def make_ndjson():
    """Build a minimal NDJSON string with assistant tool_use records and a result record.

    tool_uses entries use the raw NDJSON form: each dict must have 'name', 'id', and
    optionally 'input' (a dict whose 'file_path' key will be preserved by Step 3's changes).
    """

    def _factory(
        tool_uses: list[dict] | None = None,
        result_text: str = "done",
    ) -> str:
        records = []
        if tool_uses:
            content = [
                {
                    "type": "tool_use",
                    "name": tu["name"],
                    "id": tu["id"],
                    "input": tu.get("input", {}),
                }
                for tu in tool_uses
            ]
            records.append(json.dumps({"type": "assistant", "message": {"content": content}}))
        records.append(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": result_text,
                    "session_id": "test-session",
                }
            )
        )
        return "\n".join(records)

    return _factory


def test_parse_session_result_preserves_write_file_path(make_ndjson):
    """Write tool_use input.file_path must be preserved in tool_uses entries."""
    ndjson = make_ndjson(
        tool_uses=[{"name": "Write", "id": "tu1", "input": {"file_path": "/abs/plan.md"}}]
    )
    session = parse_session_result(ndjson)
    assert session.tool_uses == [{"name": "Write", "id": "tu1", "file_path": "/abs/plan.md"}]


def test_parse_session_result_preserves_edit_file_path(make_ndjson):
    """Edit tool_use input.file_path must be preserved in tool_uses entries."""
    ndjson = make_ndjson(
        tool_uses=[{"name": "Edit", "id": "tu2", "input": {"file_path": "/abs/file.py"}}]
    )
    session = parse_session_result(ndjson)
    assert session.tool_uses == [{"name": "Edit", "id": "tu2", "file_path": "/abs/file.py"}]


def test_parse_session_result_non_write_tools_no_file_path(make_ndjson):
    """Non-Write/Edit tool_uses must not gain a file_path key."""
    ndjson = make_ndjson(tool_uses=[{"name": "Bash", "id": "tu3", "input": {"command": "ls"}}])
    session = parse_session_result(ndjson)
    assert session.tool_uses == [{"name": "Bash", "id": "tu3"}]
    assert "file_path" not in session.tool_uses[0]


class TestMinus9ReturncodeCoverage:
    """Specification tests: returncode=-9 under COMPLETED is a valid success (1c).

    Before the fix, channel_won unconditionally sent SIGKILL (-9) to every
    COMPLETED session. These tests document that the adjudicator correctly
    classifies -9 under COMPLETED as successful — the bug was in the kill logic,
    not the adjudicator.
    """

    def test_compute_success_completed_returncode_minus_9_is_success(self) -> None:
        """COMPLETED + CHANNEL_A + returncode=-9 + content → success=True."""
        session = _make_success_session("ok")
        result = _compute_success(
            session,
            returncode=-9,
            termination=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        assert result is True

    def test_compute_success_completed_minus_9_channel_b_is_success(self) -> None:
        """COMPLETED + CHANNEL_B + returncode=-9 → success=True (bypass)."""
        session = ClaudeSessionResult(
            subtype="success",
            result="",
            is_error=False,
            session_id="b9",
        )
        result = _compute_success(
            session,
            returncode=-9,
            termination=TerminationReason.COMPLETED,
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
        )
        assert result is True

    @pytest.mark.parametrize("returncode", [0, -15, -9])
    @pytest.mark.parametrize("termination", list(TerminationReason))
    def test_compute_retry_handles_minus_9_for_all_termination_reasons(
        self,
        termination: TerminationReason,
        returncode: int,
    ) -> None:
        """_compute_retry must not raise for any (TerminationReason, returncode) pair."""
        session = ClaudeSessionResult(
            subtype="success",
            result="done. %%ORDER_UP%%",
            is_error=False,
            session_id="rc9",
            errors=[],
        )
        result = _compute_retry(session, returncode=returncode, termination=termination)
        assert isinstance(result, tuple) and len(result) == 2

    @pytest.mark.parametrize("returncode", [0, -15, -9])
    @pytest.mark.parametrize("termination", list(TerminationReason))
    def test_compute_success_handles_minus_9_for_all_termination_reasons(
        self,
        termination: TerminationReason,
        returncode: int,
    ) -> None:
        """_compute_success must not raise for any (TerminationReason, returncode) pair."""
        session = ClaudeSessionResult(
            subtype="success",
            result="done. %%ORDER_UP%%",
            is_error=False,
            session_id="rc9",
            errors=[],
        )
        result = _compute_success(
            session,
            returncode=returncode,
            termination=termination,
            completion_marker="%%ORDER_UP%%",
        )
        assert isinstance(result, bool)
