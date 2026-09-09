"""Consistency checks for API error patterns already registered in production.

Each registered generic pattern must classify consistently across stderr (non-PTY)
and stdout/NDJSON assistant messages (PTY). Observed provider failures that are not
part of the generic pattern union have independent fixture-based acceptance tests.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import (
    CLAUDE_CODE_CAPABILITIES,
    ChannelConfirmation,
    CliSubtype,
    InfraExitCategory,
    RetryReason,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless._headless_result import _build_skill_result
from autoskillit.execution.session._exit_classification import classify_infra_exit
from autoskillit.execution.session._session_model import ClaudeSessionResult, parse_session_result
from tests.execution.conftest import (
    CODEX_API_ERROR_SIGNAL_STRINGS,
    _make_synthetic_api_error_ndjson,
)
from tests.fixtures.claude_code import AUTHENTICATION_FAILED_V1, fixture_path

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_CAPS = CLAUDE_CODE_CAPABILITIES

_RATE_LIMIT_SIGNAL_STRINGS: frozenset[str] = frozenset(
    {
        "Rate limited",
        "rate_limit_exceeded",
    }
)

# Plain strings corresponding to patterns already in _KNOWN_API_ERROR_PATTERNS.
REGISTERED_API_ERROR_SIGNALS: list[str] = [
    "overloaded",
    "529",
    "503",
    "ECONNRESET",
    "ECONNREFUSED",
    "socket hang up",
    "network error",
    "connection reset",
    "socket connection was closed",
    "Rate limited",
    "rate_limit_exceeded",
    # OpenAI/Codex API error types
    *CODEX_API_ERROR_SIGNAL_STRINGS,
]


def _expected_category(signal: str) -> InfraExitCategory:
    """Return the expected InfraExitCategory for a given API error signal."""
    if signal in _RATE_LIMIT_SIGNAL_STRINGS:
        return InfraExitCategory.RATE_LIMITED
    return InfraExitCategory.API_ERROR


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


class TestRegisteredApiErrorChannelConsistency:
    """Registered generic patterns classify consistently across supported channels."""

    @pytest.mark.parametrize("signal", REGISTERED_API_ERROR_SIGNALS)
    def test_api_error_in_assistant_messages(self, signal: str) -> None:
        """PTY mode: signal in assistant_messages (via stdout NDJSON) → correct category."""
        session = _make_api_error_session(signal)
        result = _make_result(returncode=0, stderr="", stdout="")
        assert classify_infra_exit(session, result, capabilities=_CAPS) == _expected_category(
            signal
        )

    @pytest.mark.parametrize("signal", REGISTERED_API_ERROR_SIGNALS)
    def test_api_error_in_result_field(self, signal: str) -> None:
        """Signal in session.result (via parsed stdout) → correct category."""
        session = ClaudeSessionResult(
            subtype=CliSubtype.EMPTY_OUTPUT,
            is_error=True,
            result=f"Error: {signal}",
            session_id="s1",
        )
        result = _make_result(returncode=0, stderr="")
        assert classify_infra_exit(session, result, capabilities=_CAPS) == _expected_category(
            signal
        )

    @pytest.mark.parametrize("signal", REGISTERED_API_ERROR_SIGNALS)
    def test_api_error_in_errors_field(self, signal: str) -> None:
        """Signal in session.errors (via parsed stdout) → correct category."""
        session = ClaudeSessionResult(
            subtype=CliSubtype.EMPTY_OUTPUT,
            is_error=True,
            result="",
            session_id="s1",
            errors=[f"APIError: {signal}"],
        )
        result = _make_result(returncode=0, stderr="")
        assert classify_infra_exit(session, result, capabilities=_CAPS) == _expected_category(
            signal
        )

    @pytest.mark.parametrize("signal", REGISTERED_API_ERROR_SIGNALS)
    def test_api_error_in_stderr_fallback(self, signal: str) -> None:
        """Stderr fallback: signal in result.stderr → correct category via classify_infra_exit."""
        session = ClaudeSessionResult(
            subtype=CliSubtype.EMPTY_OUTPUT,
            is_error=True,
            result="",
            session_id="s1",
        )
        result = _make_result(returncode=1, stderr=f"Error: {signal}", stdout="")
        assert classify_infra_exit(session, result, capabilities=_CAPS) == _expected_category(
            signal
        )


# ---------------------------------------------------------------------------
# Invariant 2: PTY-mode end-to-end via _build_skill_result
# ---------------------------------------------------------------------------


class TestRegisteredApiErrorPtyModeEndToEnd:
    """Registered generic patterns route consistently through the Claude PTY path."""

    @pytest.mark.parametrize("signal", REGISTERED_API_ERROR_SIGNALS)
    def test_api_error_pty_mode_routes_correctly(self, signal: str) -> None:
        """Empty stderr + API error in stdout NDJSON → needs_retry=True, correct reason."""

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
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        expected_cat = _expected_category(signal)
        assert sr.infra.exit_category == expected_cat.value, (
            f"Expected infra.exit_category='{expected_cat.value}' for signal='{signal}', "
            f"got '{sr.infra.exit_category}'"
        )
        assert sr.needs_retry is True
        if signal in _RATE_LIMIT_SIGNAL_STRINGS:
            assert sr.retry_reason == RetryReason.RATE_LIMITED
        else:
            assert sr.retry_reason == RetryReason.RESUME

    def test_api_error_pty_mode_realistic_overload(self) -> None:
        """Realistic production scenario: overloaded_error, returncode=0, empty stderr."""

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
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.infra.exit_category == "api_error"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.RESUME

    def test_context_length_exceeded_pty_mode_routes_to_context_exhausted(self) -> None:
        """PTY mode: context_length_exceeded → CONTEXT_EXHAUSTED, needs_retry=True, RESUME."""

        ndjson = _make_synthetic_api_error_ndjson(
            error_type="context_length_exceeded",
            message="context_length_exceeded",
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
        sr = _build_skill_result(result, backend=ClaudeCodeBackend())
        assert sr.infra.exit_category == "context_exhausted"
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


@pytest.mark.parametrize("status", [401, 403, 404])
def test_terminal_provider_status_does_not_resume(status: int) -> None:
    result_line = json.dumps(
        {
            "type": "result",
            "subtype": "empty_output",
            "is_error": True,
            "result": "",
            "session_id": "terminal-status",
            "api_error_status": status,
        }
    )
    skill_result = _build_skill_result(
        _make_result(stdout=result_line), backend=ClaudeCodeBackend()
    )

    assert skill_result.infra.exit_category == InfraExitCategory.API_ERROR_TERMINAL.value
    assert skill_result.needs_retry is False
    assert skill_result.retry_reason is RetryReason.NONE


def test_retriable_provider_status_continues_to_resume() -> None:
    result_line = json.dumps(
        {
            "type": "result",
            "subtype": "empty_output",
            "is_error": True,
            "result": "",
            "session_id": "retriable-status",
            "api_error_status": 503,
        }
    )
    skill_result = _build_skill_result(
        _make_result(stdout=result_line), backend=ClaudeCodeBackend()
    )

    assert skill_result.infra.exit_category == InfraExitCategory.API_ERROR.value
    assert skill_result.needs_retry is True
    assert skill_result.retry_reason is RetryReason.RESUME


def test_unclassified_failure_is_visible_without_retry_override() -> None:
    result_line = json.dumps(
        {
            "type": "result",
            "subtype": "empty_output",
            "is_error": True,
            "result": "",
            "session_id": "unclassified-failure",
        }
    )
    skill_result = _build_skill_result(
        _make_result(stdout=result_line), backend=ClaudeCodeBackend()
    )

    assert skill_result.infra.exit_category == InfraExitCategory.UNCLASSIFIED.value
    assert skill_result.needs_retry is True
    assert skill_result.retry_reason is RetryReason.EMPTY_OUTPUT


def test_untyped_authentication_failure_is_terminal_end_to_end() -> None:
    skill_result = _build_skill_result(
        _make_result(stdout=fixture_path(AUTHENTICATION_FAILED_V1).read_text()),
        backend=ClaudeCodeBackend(),
    )

    assert skill_result.infra.exit_category == InfraExitCategory.API_ERROR_TERMINAL.value
    assert skill_result.needs_retry is False
    assert skill_result.api_failure.error_code == "authentication_failed"


def test_provider_failure_evidence_is_attached_to_the_skill_result() -> None:
    result_line = json.dumps(
        {
            "type": "result",
            "subtype": "empty_output",
            "is_error": True,
            "result": "",
            "session_id": "provider-evidence",
            "api_error_status": 429,
            "terminal_reason": "api_error",
        }
    )
    rate_limit_line = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "rateLimitType": "seven_day",
                "resetsAt": 1735689600,
            },
        }
    )
    skill_result = _build_skill_result(
        _make_result(stdout="\n".join([rate_limit_line, result_line])),
        backend=ClaudeCodeBackend(),
    )

    assert skill_result.api_failure.status == 429
    assert skill_result.api_failure.terminal_reason == "api_error"
    assert skill_result.api_failure.rate_limit.limit_type == "seven_day"
    assert skill_result.api_failure.rate_limit.resets_at_epoch == 1735689600


# ---------------------------------------------------------------------------
# Invariant 4: Codex context_length_exceeded routes to CONTEXT_EXHAUSTED
# ---------------------------------------------------------------------------


class TestContextExhaustedCodexInvariant:
    """context_length_exceeded must route to CONTEXT_EXHAUSTED, not API_ERROR."""

    def test_context_exhausted_in_assistant_messages(self) -> None:
        session = _make_api_error_session("context_length_exceeded")
        result = _make_result(returncode=0, stderr="", stdout="")
        assert (
            classify_infra_exit(session, result, capabilities=_CAPS)
            == InfraExitCategory.CONTEXT_EXHAUSTED
        )

    def test_context_exhausted_in_result_field(self) -> None:
        session = ClaudeSessionResult(
            subtype=CliSubtype.EMPTY_OUTPUT,
            is_error=True,
            result="Error: context_length_exceeded",
            session_id="s1",
        )
        result = _make_result(returncode=0, stderr="")
        assert (
            classify_infra_exit(session, result, capabilities=_CAPS)
            == InfraExitCategory.CONTEXT_EXHAUSTED
        )

    def test_context_exhausted_in_errors_field(self) -> None:
        session = ClaudeSessionResult(
            subtype=CliSubtype.EMPTY_OUTPUT,
            is_error=True,
            result="",
            session_id="s1",
            errors=["APIError: context_length_exceeded"],
        )
        result = _make_result(returncode=0, stderr="")
        assert (
            classify_infra_exit(session, result, capabilities=_CAPS)
            == InfraExitCategory.CONTEXT_EXHAUSTED
        )

    def test_context_exhausted_in_stderr(self) -> None:
        session = ClaudeSessionResult(
            subtype=CliSubtype.EMPTY_OUTPUT,
            is_error=True,
            result="",
            session_id="s1",
        )
        result = _make_result(returncode=1, stderr="Error: context_length_exceeded", stdout="")
        assert (
            classify_infra_exit(session, result, capabilities=_CAPS)
            == InfraExitCategory.CONTEXT_EXHAUSTED
        )


class TestApiErrorStatusChannelInvariance:
    """api_error_status structured signal must route correctly end-to-end."""

    def test_api_error_status_429_triggers_rate_limited_classification(self) -> None:
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "partial work",
                "session_id": "s1",
                "is_error": False,
                "api_error_status": 429,
            }
        )
        result = SubprocessResult(
            returncode=0,
            stdout=result_line,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        sr = _build_skill_result(result, backend=ClaudeCodeBackend(), completion_marker="DONE")
        assert sr.infra.exit_category == "rate_limited"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.RATE_LIMITED
