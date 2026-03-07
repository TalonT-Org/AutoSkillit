"""Tests for _compute_success, _compute_retry, _is_kill_anomaly, and adjudication consistency."""

from __future__ import annotations

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
    TerminationReason,
)
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _compute_retry,
    _compute_success,
    _is_kill_anomaly,
    parse_session_result,
)


def _make_success_session(result: str = "done") -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype="success",
        is_error=False,
        result=result,
        session_id="s1",
    )


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
    """NATURAL_EXIT with non-zero returncode is always a failure."""

    def test_natural_exit_nonzero_returncode_with_success_session_returns_false(self):
        """NATURAL_EXIT + non-zero returncode is unrecoverable regardless of session envelope.

        Documents that PTY-masking quirks on natural exit cannot be distinguished from
        genuine CLI errors, so we fail conservatively.
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
        """Infrastructure failure: session never ran, CLI exited cleanly."""
        session = ClaudeSessionResult(
            subtype="empty_output", is_error=True, result="", session_id=""
        )
        needs, reason = _compute_retry(
            session, returncode=0, termination=TerminationReason.NATURAL_EXIT
        )
        assert needs is True
        assert reason == RetryReason.RESUME

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
        """success + "" + NATURAL_EXIT + rc=0 must be retriable (stop-delay race).

        CLAUDE_CODE_EXIT_AFTER_STOP_DELAY causes a timer-based self-exit that produces
        NATURAL_EXIT with subtype='success' and an empty result field. The CLI writes a
        valid result envelope header before the timer fires, leaving result=''. This
        is a kill-race artifact and must retry, not silently succeed-as-failure.
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
        assert reason == RetryReason.RESUME

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
            # (premature exit scenario — Channel A marker-aware, didn't fire)
            pytest.param(
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.UNMONITORED,
                "Here is the PR context block with substantive content...",
                0,
                "success",
                False,
                False,
                False,
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
        )
        assert success == expected_success
        assert needs_retry == expected_retry

    def test_premature_exit_substantive_result_no_marker_is_failed(self) -> None:
        """NATURAL_EXIT + UNMONITORED + substantive result without marker → FAILED.

        Documents the expected post-fix behavior: when Channel A is marker-aware
        and doesn't fire on a premature exit, channel confirmation is UNMONITORED,
        and the dead-end guard does not promote to RETRIABLE.
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
        )
        assert success is False
        assert needs_retry is False
        assert reason == RetryReason.NONE

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
