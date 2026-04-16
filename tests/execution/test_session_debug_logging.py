"""Tests for debug logging instrumentation in session.py."""

from __future__ import annotations

import pytest
import structlog.testing

from autoskillit.core.types import ChannelConfirmation, TerminationReason
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _check_session_content,
    _compute_outcome,
    _compute_retry,
    _compute_success,
)

pytestmark = [pytest.mark.layer("execution")]


class TestCheckSessionContentLogging:
    """Verify _check_session_content logs exact failure reason."""

    def test_logs_is_error(self):
        session = ClaudeSessionResult(subtype="success", is_error=True, result="x", session_id="")
        with structlog.testing.capture_logs() as logs:
            result = _check_session_content(session, "")
        assert result is False
        content_logs = [r for r in logs if r.get("event") == "content_check_failed"]
        assert content_logs
        assert content_logs[0]["reason"] == "is_error"

    def test_logs_empty_result(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="  ", session_id=""
        )
        with structlog.testing.capture_logs() as logs:
            result = _check_session_content(session, "")
        assert result is False
        content_logs = [r for r in logs if r.get("event") == "content_check_failed"]
        assert content_logs
        assert content_logs[0]["reason"] == "empty_result"

    def test_logs_failure_subtype(self):
        session = ClaudeSessionResult(subtype="unknown", is_error=False, result="x", session_id="")
        with structlog.testing.capture_logs() as logs:
            result = _check_session_content(session, "")
        assert result is False
        content_logs = [r for r in logs if r.get("event") == "content_check_failed"]
        assert content_logs
        assert content_logs[0]["reason"] == "failure_subtype"

    def test_logs_marker_absent(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="some output", session_id=""
        )
        with structlog.testing.capture_logs() as logs:
            result = _check_session_content(session, "%%MARKER%%")
        assert result is False
        content_logs = [r for r in logs if r.get("event") == "content_check_failed"]
        assert content_logs
        assert content_logs[0]["reason"] == "completion_marker_absent"

    def test_logs_result_is_only_marker(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="%%MARKER%%", session_id=""
        )
        with structlog.testing.capture_logs() as logs:
            result = _check_session_content(session, "%%MARKER%%")
        assert result is False
        content_logs = [r for r in logs if r.get("event") == "content_check_failed"]
        assert content_logs
        assert content_logs[0]["reason"] == "result_is_only_marker"

    def test_logs_content_check_passed(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="done %%MARKER%%", session_id=""
        )
        with structlog.testing.capture_logs() as logs:
            result = _check_session_content(session, "%%MARKER%%")
        assert result is True
        passed_logs = [r for r in logs if r.get("event") == "content_check_passed"]
        assert passed_logs


class TestComputeOutcomeLogging:
    """Verify _compute_outcome logs guard firings."""

    def test_logs_dead_end_guard(self):
        """_compute_outcome logs when dead-end guard promotes to RETRIABLE (drain-race path)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",  # empty result — drain-race candidate; guard must promote to RETRIABLE
            session_id="",
        )
        with structlog.testing.capture_logs() as logs:
            outcome, reason = _compute_outcome(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker="%%ORDER_UP%%",
                channel_confirmation=ChannelConfirmation.CHANNEL_A,
            )
        dead_end_logs = [r for r in logs if r.get("event") == "dead_end_guard"]
        assert dead_end_logs
        assert dead_end_logs[0]["action"] == "promoted_to_retriable"

    def test_logs_dead_end_guard_terminal_not_promoted(self):
        """Dead-end guard logs terminal_failure_not_promoted for CONTRACT_VIOLATION."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Done. %%ORDER_UP%%",  # result + marker present, patterns will fail
            session_id="",
        )
        with structlog.testing.capture_logs() as logs:
            outcome, reason = _compute_outcome(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker="%%ORDER_UP%%",
                channel_confirmation=ChannelConfirmation.CHANNEL_A,
                expected_output_patterns=["verdict\\s*=\\s*(GO|NO GO)"],
            )
        dead_end_logs = [r for r in logs if r.get("event") == "dead_end_guard"]
        assert dead_end_logs
        assert dead_end_logs[0]["action"] == "terminal_failure_not_promoted"

    def test_logs_contradiction_guard(self):
        """_compute_outcome logs when contradiction guard fires."""
        # Channel B grants success, but session.needs_retry is True (error_max_turns)
        session = ClaudeSessionResult(
            subtype="error_max_turns",
            is_error=True,
            result="x",
            session_id="",
        )
        with structlog.testing.capture_logs() as logs:
            outcome, reason = _compute_outcome(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
                completion_marker="",
                channel_confirmation=ChannelConfirmation.CHANNEL_B,
            )
        contradiction_logs = [r for r in logs if r.get("event") == "contradiction_guard"]
        assert contradiction_logs
        assert contradiction_logs[0]["action"] == "demoted_success"

    def test_logs_compute_outcome_inputs(self):
        """_compute_outcome logs input state."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="done", session_id=""
        )
        with structlog.testing.capture_logs() as logs:
            _compute_outcome(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
            )
        input_logs = [r for r in logs if r.get("event") == "compute_outcome_inputs"]
        assert input_logs
        assert "success" in input_logs[0]
        assert "needs_retry" in input_logs[0]

    def test_logs_compute_outcome_result(self):
        """_compute_outcome logs final outcome."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="done", session_id=""
        )
        with structlog.testing.capture_logs() as logs:
            _compute_outcome(
                session,
                returncode=0,
                termination=TerminationReason.NATURAL_EXIT,
            )
        result_logs = [r for r in logs if r.get("event") == "compute_outcome_result"]
        assert result_logs
        assert "outcome" in result_logs[0]


class TestComputeSuccessLogging:
    """Verify _compute_success logs channel bypass and termination dispatch."""

    def test_logs_channel_b_bypass(self):
        session = ClaudeSessionResult(subtype="success", is_error=False, result="x", session_id="")
        with structlog.testing.capture_logs() as logs:
            result = _compute_success(
                session,
                0,
                TerminationReason.NATURAL_EXIT,
                "",
                ChannelConfirmation.CHANNEL_B,
            )
        assert result is True
        bypass_logs = [r for r in logs if r.get("event") == "compute_success_bypass"]
        assert bypass_logs
        assert bypass_logs[0]["channel"] == "CHANNEL_B"

    def test_logs_termination_dispatch(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="done", session_id=""
        )
        with structlog.testing.capture_logs() as logs:
            _compute_success(
                session,
                0,
                TerminationReason.NATURAL_EXIT,
                "",
                ChannelConfirmation.UNMONITORED,
            )
        term_logs = [r for r in logs if r.get("event") == "compute_success_termination"]
        assert term_logs
        assert term_logs[0]["termination"] == "NATURAL_EXIT"


class TestComputeRetryLogging:
    """Verify _compute_retry logs API signals and termination dispatch."""

    def test_logs_api_signal(self):
        session = ClaudeSessionResult(
            subtype="error_max_turns",
            is_error=True,
            result="x",
            session_id="",
        )
        with structlog.testing.capture_logs() as logs:
            needs, reason = _compute_retry(
                session,
                0,
                TerminationReason.NATURAL_EXIT,
            )
        assert needs is True
        api_logs = [r for r in logs if r.get("event") == "compute_retry_api_signal"]
        assert api_logs
        assert api_logs[0]["needs_retry"] is True

    def test_logs_termination_result(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="done", session_id=""
        )
        with structlog.testing.capture_logs() as logs:
            _compute_retry(
                session,
                0,
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.UNMONITORED,
            )
        result_logs = [r for r in logs if r.get("event") == "compute_retry_result"]
        assert result_logs
        assert result_logs[0]["termination"] == "NATURAL_EXIT"
