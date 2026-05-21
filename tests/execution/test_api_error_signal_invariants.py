"""API error signal invariants: API errors must be detected regardless of channel.

Architectural immunity test: classify_infra_exit must return API_ERROR whether the
signal arrives via stderr (non-PTY) or via stdout/NDJSON assistant messages (PTY).
Adding a new detection path that only reads one channel will cause this test to
fail immediately.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    InfraExitCategory,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.headless._headless_result import _build_skill_result
from autoskillit.execution.session._exit_classification import classify_infra_exit
from autoskillit.execution.session._session_model import ClaudeSessionResult, parse_session_result
from tests.execution.conftest import (
    CODEX_API_ERROR_SIGNAL_STRINGS,
    _make_synthetic_api_error_ndjson,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

# All patterns that _KNOWN_API_ERROR_PATTERNS covers
API_ERROR_SIGNALS: list[str] = [
    "overloaded",
    "529",
    "503",
    "ECONNRESET",
    "ECONNREFUSED",
    "socket hang up",
    "network error",
    "connection reset",
    # OpenAI/Codex API error types
    *CODEX_API_ERROR_SIGNAL_STRINGS,
]


def _make_api_error_session(signal: str) -> ClaudeSessionResult:
    """Build a ClaudeSessionResult with the API error signal in assistant_messages."""
    ndjson = _make_synthetic_api_error_ndjson(
        error_type=f"{signal}_error" if " " not in signal else signal,
        message=signal,
    )
    result_line = json.dumps(
        {
            "type": "result",
            "subtype": "empty_output",
            "is_error": True,
            "result": "",
            "session_id": "",
        }
    )
    return parse_session_result(ndjson + "\n" + result_line)


def _make_result(
    returncode: int = 0,
    stderr: str = "",
    stdout: str = "",
) -> SubprocessResult:
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
        channel_confirmation=ChannelConfirmation.UNMONITORED,
    )


# ---------------------------------------------------------------------------
# Invariant 1: Channel-agnostic detection via classify_infra_exit
# ---------------------------------------------------------------------------


class TestApiErrorChannelInvariant:
    """API errors must be classified as API_ERROR regardless of channel."""

    @pytest.mark.parametrize("signal", API_ERROR_SIGNALS)
    def test_api_error_in_assistant_messages(self, signal: str) -> None:
        """PTY mode: signal in assistant_messages (via stdout NDJSON) → API_ERROR."""
        session = _make_api_error_session(signal)
        result = _make_result(returncode=0, stderr="", stdout="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    @pytest.mark.parametrize("signal", API_ERROR_SIGNALS)
    def test_api_error_in_result_field(self, signal: str) -> None:
        """Signal in session.result (via parsed stdout) → API_ERROR."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result=f"Error: {signal}",
            session_id="s1",
        )
        result = _make_result(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    @pytest.mark.parametrize("signal", API_ERROR_SIGNALS)
    def test_api_error_in_errors_field(self, signal: str) -> None:
        """Signal in session.errors (via parsed stdout) → API_ERROR."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="s1",
            errors=[f"APIError: {signal}"],
        )
        result = _make_result(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    @pytest.mark.parametrize("signal", API_ERROR_SIGNALS)
    def test_api_error_in_stderr_fallback(self, signal: str) -> None:
        """Stderr fallback: signal in result.stderr → API_ERROR via classify_infra_exit."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="s1",
        )
        result = _make_result(returncode=1, stderr=f"Error: {signal}", stdout="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR


# ---------------------------------------------------------------------------
# Invariant 2: PTY-mode end-to-end via _build_skill_result
# ---------------------------------------------------------------------------


class TestApiErrorPtyModeEndToEnd:
    """PTY-mode subprocess result (empty stderr, API error in stdout) → RESUME."""

    @pytest.mark.parametrize("signal", API_ERROR_SIGNALS)
    def test_api_error_pty_mode_routes_to_resume(self, signal: str) -> None:
        """Empty stderr + API error in stdout NDJSON → needs_retry=True, RESUME."""
        from autoskillit.core.types import RetryReason

        ndjson = _make_synthetic_api_error_ndjson(
            error_type=f"{signal}_error" if " " not in signal else signal,
            message=signal,
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "empty_output",
                "is_error": True,
                "result": "",
                "session_id": "",
            }
        )
        result = SubprocessResult(
            returncode=0,
            stdout=ndjson + "\n" + result_line,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr = _build_skill_result(result)
        assert sr.infra.exit_category == "api_error", (
            f"Expected infra.exit_category='api_error' for signal='{signal}', "
            f"got '{sr.infra.exit_category}'"
        )
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.RESUME

    def test_api_error_pty_mode_realistic_overload(self) -> None:
        """Realistic production scenario: overloaded_error, returncode=0, empty stderr."""
        from autoskillit.core.types import RetryReason

        ndjson = _make_synthetic_api_error_ndjson(
            error_type="overloaded_error",
            message="Overloaded",
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "empty_output",
                "is_error": True,
                "result": "",
                "session_id": "",
            }
        )
        result = SubprocessResult(
            returncode=0,
            stdout=ndjson + "\n" + result_line,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr = _build_skill_result(result)
        assert sr.infra.exit_category == "api_error"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.RESUME


# ---------------------------------------------------------------------------
# Invariant 3: Structural guard — both detection methods on session model
# ---------------------------------------------------------------------------


def test_detection_methods_are_session_model_methods() -> None:
    """Both infra detection methods must be on ClaudeSessionResult, not standalone."""
    assert hasattr(ClaudeSessionResult, "_is_context_exhausted")
    assert hasattr(ClaudeSessionResult, "_has_api_error")
    assert callable(getattr(ClaudeSessionResult, "_is_context_exhausted"))
    assert callable(getattr(ClaudeSessionResult, "_has_api_error"))
