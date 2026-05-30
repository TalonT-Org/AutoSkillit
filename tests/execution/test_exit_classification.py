"""Tests for classify_infra_exit and InfraExitCategory (T1, T7)."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    InfraExitCategory,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.backends._codex_parse import CodexResultParser
from autoskillit.execution.headless._headless_evidence import _adapt_agent_result
from autoskillit.execution.session._exit_classification import (
    _CODEX_API_ERROR_PATTERNS,
    _RATE_LIMIT_PATTERNS,
    classify_infra_exit,
)
from autoskillit.execution.session._session_model import ClaudeSessionResult
from tests.execution.conftest import CODEX_API_ERROR_SIGNAL_STRINGS

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

# CODEX signals that do NOT match rate-limit patterns (must still classify as API_ERROR).
_NON_RATE_LIMIT_CODEX_SIGNALS: tuple[str, ...] = tuple(
    sig
    for sig in CODEX_API_ERROR_SIGNAL_STRINGS
    if not any(p.search(sig) for p in _RATE_LIMIT_PATTERNS)
)
# CODEX signals that DO match rate-limit patterns (must classify as RATE_LIMITED).
_RATE_LIMIT_CODEX_SIGNALS: tuple[str, ...] = tuple(
    sig
    for sig in CODEX_API_ERROR_SIGNAL_STRINGS
    if any(p.search(sig) for p in _RATE_LIMIT_PATTERNS)
)


def _sr(
    returncode: int = 0,
    stderr: str = "",
    termination: TerminationReason = TerminationReason.NATURAL_EXIT,
) -> SubprocessResult:
    return SubprocessResult(
        returncode=returncode,
        stdout="",
        stderr=stderr,
        termination=termination,
        pid=12345,
        channel_confirmation=ChannelConfirmation.UNMONITORED,
    )


def _turn_failed_ndjson(error_message: str) -> str:
    return json.dumps({"type": "turn.failed", "error": {"message": error_message}})


class TestClassifyInfraExit:
    def test_context_exhausted_from_jsonl_flag(self):
        """jsonl_context_exhausted=True → CONTEXT_EXHAUSTED."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=True,
            result="prompt is too long",
            session_id="s1",
            jsonl_context_exhausted=True,
        )
        result = _sr(returncode=1, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.CONTEXT_EXHAUSTED

    def test_api_error_overloaded_in_assistant_messages(self):
        """API error in assistant_messages → API_ERROR (PTY-safe path)."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            assistant_messages=[
                "API Error: "
                '{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
            ],
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    def test_api_error_529_in_assistant_messages(self):
        """HTTP 529 in assistant_messages → API_ERROR."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            assistant_messages=["HTTP Error 529: Service Overloaded"],
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    def test_api_error_econnreset_in_assistant_messages(self):
        """ECONNRESET in assistant_messages → API_ERROR."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            assistant_messages=["Error: read ECONNRESET"],
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    def test_api_error_socket_connection_closed_in_assistant_messages(self):
        """Bun socket-closed error in assistant_messages → API_ERROR."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            assistant_messages=[
                "The socket connection was closed unexpectedly. "
                "For more information, pass 'verbose: true' in the second argument to fetch()",
            ],
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    def test_process_killed_sigkill(self):
        """returncode=-9 (SIGKILL) → PROCESS_KILLED."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
        )
        result = _sr(returncode=-9, stderr="Killed")
        assert classify_infra_exit(session, result) == InfraExitCategory.PROCESS_KILLED

    def test_process_killed_sigterm(self):
        """returncode=-15 (SIGTERM, NOT from autoskillit kill) → PROCESS_KILLED."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
        )
        result = _sr(returncode=-15, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.PROCESS_KILLED

    def test_completed_success(self):
        """Normal success → COMPLETED."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Done.",
            session_id="s1",
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.COMPLETED

    def test_api_retry_exhausted_returns_api_error(self):
        """api_retry_exhausted=True → API_ERROR."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            api_retry_exhausted=True,
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    def test_api_retry_not_exhausted_does_not_promote(self):
        """api_retry_exhausted=False with retries present → no promotion."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Done.",
            session_id="s1",
            api_retry_count=3,
            api_retry_exhausted=False,
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.COMPLETED

    def test_completed_logical_failure(self):
        """Agent failure (success=false, explicit error) → COMPLETED (not infra)."""
        session = ClaudeSessionResult(
            subtype="error",
            is_error=True,
            result="Could not find file",
            session_id="s1",
        )
        result = _sr(returncode=1, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.COMPLETED

    def test_context_exhaustion_takes_precedence_over_api_error(self):
        """Both signals present → CONTEXT_EXHAUSTED wins (more specific)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=True,
            result="prompt is too long",
            session_id="s1",
            jsonl_context_exhausted=True,
            assistant_messages=[
                "API Error: "
                '{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
            ],
        )
        result = _sr(returncode=1, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.CONTEXT_EXHAUSTED

    def test_context_exhausted_from_codex_context_length_exceeded(self):
        """context_length_exceeded in errors → CONTEXT_EXHAUSTED (not API_ERROR)."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="s1",
            errors=["context_length_exceeded"],
        )
        result = _sr(returncode=1, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.CONTEXT_EXHAUSTED

    @pytest.mark.parametrize("signal", _NON_RATE_LIMIT_CODEX_SIGNALS)
    def test_api_error_openai_patterns_in_stderr(self, signal: str) -> None:
        """Non-rate-limit OpenAI/Codex error pattern in stderr → API_ERROR."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
        )
        result = _sr(returncode=1, stderr=f"Error: {signal}")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    @pytest.mark.parametrize("signal", _NON_RATE_LIMIT_CODEX_SIGNALS)
    def test_api_error_openai_patterns_in_assistant_messages(self, signal: str) -> None:
        """Non-rate-limit OpenAI/Codex error pattern in assistant_messages → API_ERROR."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            assistant_messages=[
                f'OpenAI API Error: {{"type":"{signal}","message":"{signal}"}}',
            ],
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    @pytest.mark.parametrize("signal", _RATE_LIMIT_CODEX_SIGNALS)
    def test_rate_limit_codex_patterns_in_stderr(self, signal: str) -> None:
        """Rate-limit Codex error pattern in stderr → RATE_LIMITED."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
        )
        result = _sr(returncode=1, stderr=f"Error: {signal}")
        assert classify_infra_exit(session, result) == InfraExitCategory.RATE_LIMITED

    @pytest.mark.parametrize("signal", _RATE_LIMIT_CODEX_SIGNALS)
    def test_rate_limit_codex_patterns_in_assistant_messages(self, signal: str) -> None:
        """Rate-limit Codex error pattern in assistant_messages → RATE_LIMITED."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            assistant_messages=[
                f'OpenAI API Error: {{"type":"{signal}","message":"{signal}"}}',
            ],
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.RATE_LIMITED


class TestCodexContextExhaustion:
    """Codex context-exhaustion boundary: context_length_exceeded substring →
    CONTEXT_EXHAUSTED, rate_limit_exceeded → RATE_LIMITED."""

    def test_context_length_exceeded_in_errors_produces_context_exhausted(self):
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="s1",
            errors=["context_length_exceeded error"],
        )
        result = _sr(returncode=1, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.CONTEXT_EXHAUSTED

    def test_rate_limit_exceeded_is_rate_limited_not_context_exhausted(self):
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="s1",
            errors=["rate_limit_exceeded"],
        )
        result = _sr(returncode=1, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.RATE_LIMITED


@pytest.mark.parametrize("category", list(InfraExitCategory))
def test_all_infra_categories_handled(category: InfraExitCategory) -> None:
    """Every InfraExitCategory value has a distinct test above."""
    assert category.value in {
        "completed",
        "context_exhausted",
        "api_error",
        "process_killed",
        "rate_limited",
    }


class TestCodexContextExhaustionFromTurnFailed:
    """Full pipeline: raw NDJSON -> CodexResultParser -> _adapt_agent_result -> classification."""

    def test_turn_failed_context_length_exceeded_sets_jsonl_context_exhausted(self) -> None:
        ndjson = _turn_failed_ndjson("context_length_exceeded")
        agent_result = CodexResultParser().parse_stdout(ndjson)
        adapted = _adapt_agent_result(agent_result)
        assert adapted.jsonl_context_exhausted is True

    def test_turn_failed_context_length_exceeded_is_context_exhausted(self) -> None:
        ndjson = _turn_failed_ndjson("context_length_exceeded")
        agent_result = CodexResultParser().parse_stdout(ndjson)
        adapted = _adapt_agent_result(agent_result)
        assert adapted._is_context_exhausted() is True

    def test_turn_failed_context_length_exceeded_classify_returns_context_exhausted(self) -> None:
        ndjson = _turn_failed_ndjson("context_length_exceeded")
        agent_result = CodexResultParser().parse_stdout(ndjson)
        adapted = _adapt_agent_result(agent_result)
        result = _sr(returncode=1)
        assert classify_infra_exit(adapted, result) == InfraExitCategory.CONTEXT_EXHAUSTED

    def test_turn_failed_rate_limit_exceeded_jsonl_context_exhausted_false(self) -> None:
        ndjson = _turn_failed_ndjson("rate_limit_exceeded")
        agent_result = CodexResultParser().parse_stdout(ndjson)
        adapted = _adapt_agent_result(agent_result)
        assert adapted.jsonl_context_exhausted is False


class TestRateLimitClassification:
    def test_rate_limited_text_classified_as_rate_limited(self) -> None:
        """Session text containing 'rate limited' → RATE_LIMITED (not API_ERROR)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=(
                "API Error: Server is temporarily limiting requests"
                " (not your usage limit) · Rate limited"
            ),
            session_id="s1",
            assistant_messages=["Rate limited"],
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.RATE_LIMITED

    def test_api_error_status_429_classified_as_rate_limited(self) -> None:
        """api_error_status=429 → RATE_LIMITED (not API_ERROR)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="done",
            session_id="s1",
            api_error_status=429,
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.RATE_LIMITED

    def test_api_error_status_400_classified_as_api_error(self) -> None:
        """api_error_status=400 (bad request) → API_ERROR (not RATE_LIMITED)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="done",
            session_id="s1",
            api_error_status=400,
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    def test_api_error_status_below_400_does_not_trigger(self) -> None:
        """api_error_status=200 → COMPLETED (no infra failure)."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="done",
            session_id="s1",
            api_error_status=200,
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.COMPLETED

    def test_429_with_api_retry_exhausted_still_rate_limited(self) -> None:
        """api_error_status=429 AND api_retry_exhausted=True → RATE_LIMITED.

        The 429 check precedes api_retry_exhausted so exhausted retries on a
        rate-limit do not downgrade to API_ERROR.
        """
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="s1",
            api_error_status=429,
            api_retry_exhausted=True,
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.RATE_LIMITED

    def test_rate_limited_text_in_result_no_status_code(self) -> None:
        """'rate limited' text in result (no numeric status) → RATE_LIMITED."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="Error: You are being rate limited. Please wait.",
            session_id="s1",
        )
        result = _sr(returncode=0, stderr="")
        assert classify_infra_exit(session, result) == InfraExitCategory.RATE_LIMITED


def test_codex_api_error_patterns_count() -> None:
    """Structural test: _CODEX_API_ERROR_PATTERNS has exactly 4 entries."""
    assert len(_CODEX_API_ERROR_PATTERNS) == 4
