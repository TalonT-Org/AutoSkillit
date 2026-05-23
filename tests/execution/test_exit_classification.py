"""Tests for classify_infra_exit and InfraExitCategory (T1, T7)."""

from __future__ import annotations

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    InfraExitCategory,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.session._exit_classification import (
    _CODEX_API_ERROR_PATTERNS,
    classify_infra_exit,
)
from autoskillit.execution.session._session_model import ClaudeSessionResult
from tests.execution.conftest import CODEX_API_ERROR_SIGNAL_STRINGS

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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

    @pytest.mark.parametrize("signal", CODEX_API_ERROR_SIGNAL_STRINGS)
    def test_api_error_openai_patterns_in_stderr(self, signal: str) -> None:
        """OpenAI/Codex error pattern in stderr → API_ERROR."""
        session = ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
        )
        result = _sr(returncode=1, stderr=f"Error: {signal}")
        assert classify_infra_exit(session, result) == InfraExitCategory.API_ERROR

    @pytest.mark.parametrize("signal", CODEX_API_ERROR_SIGNAL_STRINGS)
    def test_api_error_openai_patterns_in_assistant_messages(self, signal: str) -> None:
        """OpenAI/Codex error pattern in assistant_messages → API_ERROR."""
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


@pytest.mark.parametrize("category", list(InfraExitCategory))
def test_all_infra_categories_handled(category: InfraExitCategory) -> None:
    """Every InfraExitCategory value has a distinct test above."""
    assert category.value in {"completed", "context_exhausted", "api_error", "process_killed"}


def test_codex_api_error_patterns_count() -> None:
    """Structural test: _CODEX_API_ERROR_PATTERNS has exactly 4 entries."""
    assert len(_CODEX_API_ERROR_PATTERNS) == 4
