"""Tests for _compute_success adjudication logic."""

from __future__ import annotations

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    TerminationReason,
)
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _compute_retry,
    _compute_success,
    parse_session_result,
)
from tests.execution.conftest import _make_success_session

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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
