"""L1 unit tests for execution/session.py — session result parsing and policy functions."""

from __future__ import annotations

import json
import sys

import pytest
import structlog

from autoskillit.core.types import (
    CONTEXT_EXHAUSTION_MARKER,
    ChannelConfirmation,
    RetryReason,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.session import (
    ClaudeSessionResult,
    SkillResult,
    _compute_retry,
    _compute_success,
    _is_kill_anomaly,
    extract_token_usage,
    parse_session_result,
)
from autoskillit.server.tools_execution import run_skill_retry


def _make_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    termination_reason: TerminationReason = TerminationReason.NATURAL_EXIT,
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.CHANNEL_B,
) -> SubprocessResult:
    """Create a SubprocessResult for mocking run_managed_async."""
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=termination_reason,
        pid=12345,
        channel_confirmation=channel_confirmation,
    )


class TestClaudeSessionResult:
    """ClaudeSessionResult correctly parses Claude Code JSON output."""

    def test_parses_success_result(self):
        """Normal completion extracts result and session_id."""
        raw = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Done.",
            "session_id": "abc-123",
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.subtype == "success"
        assert parsed.is_error is False
        assert parsed.result == "Done."
        assert parsed.session_id == "abc-123"
        assert parsed.needs_retry is False
        assert parsed.retry_reason == RetryReason.NONE

    def test_parses_error_max_turns(self):
        """Turn limit produces needs_retry=True with reason=RESUME."""
        raw = {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": False,
            "session_id": "abc-123",
            "errors": ["Max turns reached"],
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.subtype == "error_max_turns"
        assert parsed.needs_retry is True
        assert parsed.retry_reason == RetryReason.RESUME
        assert parsed.result == ""

    def test_parses_prompt_too_long(self):
        """Context exhaustion produces needs_retry=True with reason=RESUME."""
        raw = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "result": "Prompt is too long",
            "session_id": "abc-123",
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.is_error is True
        assert parsed.needs_retry is True
        assert parsed.retry_reason == RetryReason.RESUME

    def test_parses_execution_error_not_retriable(self):
        """Runtime errors are not automatically retriable."""
        raw = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "session_id": "abc-123",
            "errors": ["Tool execution failed"],
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.subtype == "error_during_execution"
        assert parsed.needs_retry is False
        assert parsed.retry_reason == RetryReason.NONE

    def test_non_json_stdout_is_error(self):
        """Non-JSON output (crashes, tracebacks) is always an error."""
        parsed = parse_session_result("Traceback (most recent call last):\n  File...")
        assert parsed.is_error is True
        assert parsed.subtype == "unparseable"
        assert "Traceback" in parsed.result
        assert parsed.needs_retry is False

    def test_empty_stdout_is_error(self):
        """Empty stdout means the session produced no output — always an error."""
        parsed = parse_session_result("")
        assert parsed.is_error is True
        assert parsed.subtype == "empty_output"
        assert parsed.result == ""
        assert parsed.needs_retry is False

    def test_whitespace_only_stdout_is_error(self):
        """Whitespace-only stdout is treated as empty."""
        parsed = parse_session_result("  \n  \t  ")
        assert parsed.is_error is True
        assert parsed.subtype == "empty_output"

    def test_json_without_type_result_is_error(self):
        """JSON that isn't a Claude result object is rejected by fallback."""
        parsed = parse_session_result('{"some": "random", "json": true}')
        assert parsed.is_error is True
        assert parsed.subtype == "unparseable"

    def test_non_dict_json_is_error(self):
        """Non-dict JSON (list, string, number) is unparseable."""
        parsed = parse_session_result("[1, 2, 3]")
        assert parsed.is_error is True
        assert parsed.subtype == "unparseable"

    def test_handles_ndjson_with_multiple_lines(self):
        """Parser finds type=result in multi-line NDJSON output."""
        lines = [
            json.dumps({"type": "assistant", "message": "working..."}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "Done.",
                    "session_id": "s1",
                }
            ),
        ]
        parsed = parse_session_result("\n".join(lines))
        assert parsed.subtype == "success"
        assert parsed.result == "Done."
        assert parsed.session_id == "s1"

    def test_needs_retry_and_retry_reason_are_consistent(self):
        """retry_reason is RESUME iff needs_retry is True, NONE otherwise."""
        cases = [
            ("success", False, "Done."),
            ("error_max_turns", False, ""),
            ("success", True, "Prompt is too long"),
            ("error_during_execution", True, "crashed"),
            ("unknown", False, ""),
        ]
        for subtype, is_error, result_text in cases:
            session = ClaudeSessionResult(
                subtype=subtype,
                is_error=is_error,
                result=result_text,
                session_id="s1",
            )
            if session.needs_retry:
                assert session.retry_reason == RetryReason.RESUME, (
                    f"needs_retry=True but retry_reason={session.retry_reason!r} "
                    f"for subtype={subtype}, is_error={is_error}"
                )
            else:
                assert session.retry_reason == RetryReason.NONE, (
                    f"needs_retry=False but retry_reason={session.retry_reason!r} "
                    f"for subtype={subtype}, is_error={is_error}"
                )

    def test_all_retriable_cases_produce_same_retry_reason(self):
        """Every condition that triggers needs_retry must produce the same retry_reason."""
        max_turns_case = ClaudeSessionResult(
            subtype="error_max_turns",
            is_error=False,
            result="",
            session_id="s1",
        )
        context_case = ClaudeSessionResult(
            subtype="success",
            is_error=True,
            result="Prompt is too long",
            session_id="s2",
        )
        assert max_turns_case.needs_retry is True
        assert context_case.needs_retry is True
        assert max_turns_case.retry_reason == context_case.retry_reason


class TestAgentResult:
    """agent_result produces actionable text for LLM callers."""

    def test_rewrites_context_exhaustion(self):
        """Context exhaustion result must NOT contain 'Prompt is too long'."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=True,
            result="Prompt is too long",
            session_id="s1",
        )
        assert "prompt is too long" not in session.agent_result.lower()
        assert "context" in session.agent_result.lower()
        assert "continue" in session.agent_result.lower()

    def test_rewrites_max_turns(self):
        """Max turns result must describe the situation, not pass through empty string."""
        session = ClaudeSessionResult(
            subtype="error_max_turns",
            is_error=False,
            result="",
            session_id="s1",
        )
        assert (
            "turn limit" in session.agent_result.lower()
            or "resume" in session.agent_result.lower()
        )

    def test_preserves_normal_result(self):
        """Normal success result passes through unchanged."""
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Task completed. Created 3 files.",
            session_id="s1",
        )
        assert session.agent_result == "Task completed. Created 3 files."

    def test_preserves_error_result_when_not_retriable(self):
        """Non-retriable errors pass through unchanged."""
        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="Tool execution failed: permission denied",
            session_id="s1",
        )
        assert session.agent_result == "Tool execution failed: permission denied"


class TestResponseFieldsAreTypeSafe:
    """Every discriminator field in MCP tool responses uses enum values."""

    @pytest.mark.anyio
    async def test_retry_reason_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
                "num_turns": 200,
                "errors": [],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}

    @pytest.mark.anyio
    async def test_retry_reason_none_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
                "num_turns": 50,
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}


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

    def test_empty_result_is_failure(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="", session_id="s1"
        )
        assert (
            _compute_success(session, returncode=0, termination=TerminationReason.NATURAL_EXIT)
            is False
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


class TestClaudeSessionResultTypeEnforcement:
    """ClaudeSessionResult.__post_init__ enforces field types."""

    def test_null_result_becomes_empty_string(self):
        session = ClaudeSessionResult(subtype="error", is_error=True, result=None, session_id="s1")
        assert session.result == ""

    def test_null_errors_becomes_empty_list(self):
        session = ClaudeSessionResult(
            subtype="error", is_error=True, result="err", session_id="s1", errors=None
        )
        assert session.errors == []

    def test_null_subtype_becomes_unknown(self):
        session = ClaudeSessionResult(subtype=None, is_error=False, result="ok", session_id="s1")
        assert session.subtype == "unknown"

    def test_null_session_id_becomes_empty(self):
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="ok", session_id=None
        )
        assert session.session_id == ""

    def test_is_context_exhausted_with_null_safe_fields(self):
        session = ClaudeSessionResult(
            subtype="error", is_error=True, result=None, session_id="s1", errors=None
        )
        assert session._is_context_exhausted() is False

    def test_list_content_result_becomes_string(self):
        blocks = [{"type": "text", "text": "Task completed."}]
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result=blocks, session_id="s1"
        )
        assert session.result == "Task completed."
        assert isinstance(session.result, str)


class TestParseSessionResultNullFields:
    """parse_session_result handles null JSON values correctly."""

    def test_null_result_field(self):
        raw = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": None,
            "session_id": "s1",
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.result == ""

    def test_null_errors_field(self):
        raw = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "s1",
            "errors": None,
        }
        parsed = parse_session_result(json.dumps(raw))
        assert parsed.errors == []


class TestExtractTokenUsage:
    """Tests for extract_token_usage()."""

    def test_single_assistant_record(self):
        """Single assistant record produces correct totals and model breakdown."""
        stdout = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 10,
                        "cache_read_input_tokens": 5,
                    },
                },
            }
        )
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert result["cache_creation_input_tokens"] == 10
        assert result["cache_read_input_tokens"] == 5
        assert result["model_breakdown"] == {
            "claude-sonnet-4-6": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 5,
            }
        }

    def test_multiple_assistant_records_same_model(self):
        """Multiple turns with same model accumulate correctly."""
        line1 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 40,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        line2 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 60,
                        "cache_creation_input_tokens": 20,
                        "cache_read_input_tokens": 10,
                    },
                },
            }
        )
        stdout = line1 + "\n" + line2
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 100
        assert result["cache_creation_input_tokens"] == 20
        assert result["cache_read_input_tokens"] == 10
        assert "claude-sonnet-4-6" in result["model_breakdown"]
        assert result["model_breakdown"]["claude-sonnet-4-6"]["input_tokens"] == 300

    def test_multiple_models(self):
        """Assistant records with different models produce per-model breakdown."""
        line1 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 30,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        line2 = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-4-6",
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 70,
                        "cache_creation_input_tokens": 5,
                        "cache_read_input_tokens": 15,
                    },
                },
            }
        )
        stdout = line1 + "\n" + line2
        result = extract_token_usage(stdout)
        assert result is not None
        assert "claude-sonnet-4-6" in result["model_breakdown"]
        assert "claude-opus-4-6" in result["model_breakdown"]
        assert result["model_breakdown"]["claude-sonnet-4-6"]["input_tokens"] == 100
        assert result["model_breakdown"]["claude-opus-4-6"]["input_tokens"] == 200
        # totals summed from both models (no result record present)
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 100

    def test_result_record_usage_preferred_for_totals(self):
        """When result record has usage, it provides the top-level totals."""
        assistant_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 40,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                "usage": {
                    "input_tokens": 999,
                    "output_tokens": 888,
                    "cache_creation_input_tokens": 50,
                    "cache_read_input_tokens": 25,
                },
            }
        )
        stdout = assistant_line + "\n" + result_line
        result = extract_token_usage(stdout)
        assert result is not None
        # result record totals take precedence over assistant sum
        assert result["input_tokens"] == 999
        assert result["output_tokens"] == 888
        assert result["cache_creation_input_tokens"] == 50
        assert result["cache_read_input_tokens"] == 25
        # model breakdown still comes from assistant records
        assert "claude-sonnet-4-6" in result["model_breakdown"]

    def test_fallback_to_assistant_sum_when_no_result_usage(self):
        """When result record lacks usage, top-level totals are summed from assistants."""
        assistant_line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 150,
                        "output_tokens": 60,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        )
        result_line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                # no "usage" key
            }
        )
        stdout = assistant_line + "\n" + result_line
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["input_tokens"] == 150
        assert result["output_tokens"] == 60

    def test_no_usage_data_returns_none(self):
        """Stdout with no usage records at all returns None."""
        stdout = json.dumps({"type": "user", "message": {"content": "hello"}})
        result = extract_token_usage(stdout)
        assert result is None

    def test_empty_stdout_returns_none(self):
        """Empty string returns None."""
        assert extract_token_usage("") is None

    def test_non_json_stdout_returns_none(self):
        """Non-parseable stdout returns None."""
        assert extract_token_usage("not json at all\nstill not json") is None

    def test_cache_tokens_default_to_zero(self):
        """Missing cache token fields default to 0, not omitted."""
        stdout = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 80,
                        "output_tokens": 20,
                        # cache fields absent
                    },
                },
            }
        )
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["cache_creation_input_tokens"] == 0
        assert result["cache_read_input_tokens"] == 0
        breakdown = result["model_breakdown"]["claude-sonnet-4-6"]
        assert breakdown["cache_creation_input_tokens"] == 0
        assert breakdown["cache_read_input_tokens"] == 0

    def test_ignores_non_assistant_non_result_records(self):
        """user and system records are skipped."""
        user_line = json.dumps({"type": "user", "message": {"content": "do something"}})
        system_line = json.dumps({"type": "system", "subtype": "init"})
        stdout = user_line + "\n" + system_line
        result = extract_token_usage(stdout)
        assert result is None


class TestClaudeSessionResultTokenUsage:
    """Token usage field on ClaudeSessionResult."""

    def test_default_is_none(self):
        """token_usage defaults to None when not provided."""
        session = ClaudeSessionResult(
            subtype="success", is_error=False, result="Done.", session_id="s1"
        )
        assert session.token_usage is None

    def test_preserves_token_usage_dict(self):
        """token_usage dict is stored and accessible."""
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 5,
            "model_breakdown": {"claude-sonnet-4-6": {"input_tokens": 100, "output_tokens": 50}},
        }
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="Done.",
            session_id="s1",
            token_usage=usage,
        )
        assert session.token_usage is usage
        assert session.token_usage["input_tokens"] == 100
        assert "model_breakdown" in session.token_usage


# ---------------------------------------------------------------------------
# Merged from test_session_result.py — helpers
# ---------------------------------------------------------------------------


def _make_success_session(result: str = "done") -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype="success",
        is_error=False,
        result=result,
        session_id="s1",
    )


def _make_error_session(
    subtype: str = "error_during_execution",
    result: str = "failed",
    errors: list[str] | None = None,
) -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype=subtype,
        is_error=True,
        result=result,
        session_id="s1",
        errors=errors or [],
    )


def _result_ndjson(
    result_text: str = "done",
    subtype: str = "success",
    is_error: bool = False,
    session_id: str = "s1",
    errors: list | None = None,
    usage: dict | None = None,
) -> str:
    obj: dict = {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": result_text,
        "session_id": session_id,
        "errors": errors or [],
    }
    if usage:
        obj["usage"] = usage
    return json.dumps(obj)


def _assistant_ndjson(
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_create: int = 0,
    cache_read: int = 0,
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": cache_create,
                    "cache_read_input_tokens": cache_read,
                },
            },
        }
    )


# ---------------------------------------------------------------------------
# Merged from test_session_result.py — ClaudeSessionResult contracts
# ---------------------------------------------------------------------------


class TestClaudeSessionResultBasic:
    def test_basic_fields(self):
        s = ClaudeSessionResult(
            subtype="success", is_error=False, result="hello", session_id="abc"
        )
        assert s.subtype == "success"
        assert s.is_error is False
        assert s.result == "hello"
        assert s.session_id == "abc"

    def test_post_init_coerces_list_content_to_str(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=[{"type": "text", "text": "hello"}],
            session_id="s1",
        )
        assert s.result == "hello"

    def test_post_init_coerces_none_result_to_empty_str(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=None,
            session_id="s1",  # type: ignore[arg-type]
        )
        assert s.result == ""

    def test_post_init_coerces_non_list_errors(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="ok",
            session_id="s1",
            errors=None,  # type: ignore[arg-type]
        )
        assert s.errors == []

    def test_post_init_list_with_non_dict_element(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=["plain string", {"type": "text", "text": "extra"}],
            session_id="s1",
        )
        assert s.result == "plain string\nextra"


class TestClaudeSessionResultContextExhausted:
    def test_is_context_exhausted_via_errors_list(self):
        s = ClaudeSessionResult(
            subtype="error",
            is_error=True,
            result="",
            session_id="s1",
            errors=[f"Request failed: {CONTEXT_EXHAUSTION_MARKER}"],
        )
        assert s._is_context_exhausted() is True

    def test_is_context_exhausted_via_result_text(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=True,
            result=f"Something: {CONTEXT_EXHAUSTION_MARKER}",
            session_id="s1",
        )
        assert s._is_context_exhausted() is True

    def test_is_context_exhausted_false_when_no_error(self):
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result=f"contains {CONTEXT_EXHAUSTION_MARKER} text",
            session_id="s1",
        )
        assert s._is_context_exhausted() is False


class TestClaudeSessionResultNeedsRetry:
    def test_needs_retry_true_for_max_turns(self):
        s = ClaudeSessionResult(
            subtype="error_max_turns", is_error=False, result="r", session_id="s1"
        )
        assert s.needs_retry is True

    def test_needs_retry_true_for_context_exhausted(self):
        s = ClaudeSessionResult(
            subtype="error",
            is_error=True,
            result="",
            session_id="s1",
            errors=[CONTEXT_EXHAUSTION_MARKER],
        )
        assert s.needs_retry is True

    def test_needs_retry_false_for_success(self):
        s = _make_success_session()
        assert s.needs_retry is False

    def test_retry_reason_resume_when_needs_retry(self):
        s = ClaudeSessionResult(
            subtype="error_max_turns", is_error=False, result="r", session_id="s1"
        )
        assert s.retry_reason == RetryReason.RESUME

    def test_retry_reason_none_when_no_retry(self):
        s = _make_success_session()
        assert s.retry_reason == RetryReason.NONE


# ---------------------------------------------------------------------------
# Merged from test_session_result.py — TestParseSessionResult
# ---------------------------------------------------------------------------


def _flush_logger_proxy_caches() -> None:
    """Reconnect autoskillit module-level loggers to the current structlog config.

    Two separate caching mechanisms break capture_logs() after configure_logging():

    1. BoundLoggerLazyProxy: configure_logging() (cache_logger_on_first_use=True)
       replaces proxy.bind with a finalized_bind closure. reset_defaults() creates
       a new processor list but does NOT remove the closure. Fix: pop "bind" from
       the proxy's __dict__ so the next call re-evaluates from global config.

    2. BoundLoggerFilteringAtNotset (returned by proxy.bind()):
       Holds _processors as a reference to the processor list at bind() time.
       reset_defaults() creates a new list — _processors is orphaned. Fix: reset
       _processors to the current default processor list (which capture_logs()
       modifies in-place).
    """
    import structlog._config as _sc

    current_procs = structlog.get_config()["processors"]

    for mod_name in list(sys.modules):
        if not mod_name.startswith("autoskillit"):
            continue
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for attr_name in ("logger", "_logger"):
            lg = getattr(mod, attr_name, None)
            if lg is None:
                continue
            if isinstance(lg, _sc.BoundLoggerLazyProxy):
                lg.__dict__.pop("bind", None)
            elif hasattr(lg, "_processors"):
                lg._processors = current_procs


class TestParseSessionResult:
    @pytest.fixture(autouse=True)
    def _reset_structlog(self):
        structlog.reset_defaults()
        _flush_logger_proxy_caches()
        yield
        structlog.reset_defaults()
        _flush_logger_proxy_caches()

    def test_empty_string_returns_empty_output_subtype(self):
        result = parse_session_result("")
        assert result.subtype == "empty_output"
        assert result.is_error is True

    def test_whitespace_only_returns_empty_output_subtype(self):
        result = parse_session_result("   \n\t  ")
        assert result.subtype == "empty_output"

    def test_valid_ndjson_last_result_wins(self):
        first = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "first",
                "is_error": False,
                "session_id": "a",
            }
        )
        second = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "second",
                "is_error": False,
                "session_id": "b",
            }
        )
        result = parse_session_result(first + "\n" + second)
        assert result.result == "second"
        assert result.session_id == "b"

    def test_no_result_type_line_returns_unparseable(self):
        result = parse_session_result(
            json.dumps({"type": "assistant", "message": {"content": "hello"}})
        )
        assert result.subtype == "unparseable"

    def test_non_json_returns_unparseable(self):
        result = parse_session_result("Traceback (most recent call last):\n  boom")
        assert result.subtype == "unparseable"
        assert result.is_error is True

    def test_fallback_single_json_object(self):
        single = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "ok",
                "is_error": False,
                "session_id": "s",
            }
        )
        result = parse_session_result(single)
        assert result.result == "ok"

    def test_populates_token_usage(self):
        assistant = _assistant_ndjson(input_tokens=100, output_tokens=50)
        result_rec = _result_ndjson(
            usage={
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        )
        stdout = assistant + "\n" + result_rec
        result = parse_session_result(stdout)
        assert result.token_usage is not None
        assert result.token_usage["input_tokens"] == 200

    def test_logs_unknown_result_keys_at_debug(self):
        record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "abc",
                "errors": [],
                "future_field": "x",
            }
        )
        with structlog.testing.capture_logs() as logs:
            parse_session_result(record)
        debug_entries = [e for e in logs if e.get("log_level") == "debug"]
        assert any(e.get("event") == "unknown_result_keys" for e in debug_entries)
        matched = next(e for e in debug_entries if e.get("event") == "unknown_result_keys")
        assert "future_field" in matched.get("unknown_fields", [])

    def test_no_debug_log_for_known_result_keys(self):
        record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "abc",
                "errors": [],
            }
        )
        with structlog.testing.capture_logs() as logs:
            parse_session_result(record)
        assert not any(e.get("event") == "unknown_result_keys" for e in logs)

    def test_parse_session_result_captures_assistant_messages(self):
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant","content":"Full report here."}}\n'
            '{"type":"result","subtype":"success","result":"%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = parse_session_result(ndjson)
        assert result.result == "%%ORDER_UP%%"
        assert result.assistant_messages == ["Full report here."]

    def test_parse_session_result_collects_multiple_assistant_messages(self):
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant","content":"GO verdict."}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"%%ORDER_UP%%"}}\n'
            '{"type":"result","subtype":"success","result":"%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = parse_session_result(ndjson)
        assert result.assistant_messages == ["GO verdict.", "%%ORDER_UP%%"]

    def test_parse_session_result_assistant_messages_empty_when_no_assistant_records(self):
        ndjson = (
            '{"type":"result","subtype":"success","result":"Done.\\n\\n%%ORDER_UP%%",'
            '"session_id":"s1","is_error":false}\n'
        )
        result = parse_session_result(ndjson)
        assert result.assistant_messages == []


# ---------------------------------------------------------------------------
# Merged from test_session_result.py — token usage architecture
# ---------------------------------------------------------------------------


class TestExtractTokenUsageArchitecture:
    """Contract tests asserting extract_token_usage's construction-time role."""

    def test_token_usage_on_parsed_result_matches_standalone_extract(self):
        assistant = _assistant_ndjson(input_tokens=100, output_tokens=50, cache_create=10)
        result_rec = _result_ndjson(
            usage={
                "input_tokens": 999,
                "output_tokens": 888,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 0,
            }
        )
        stdout = assistant + "\n" + result_rec

        parsed = parse_session_result(stdout)
        standalone = extract_token_usage(stdout)

        assert parsed.token_usage == standalone

    def test_token_usage_none_when_no_usage_in_stdout(self):
        stdout = _result_ndjson()  # no usage key in result record
        parsed = parse_session_result(stdout)
        assert parsed.token_usage is None


# ---------------------------------------------------------------------------
# Merged from test_session_result.py — unique compute_success tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Merged from test_session_result.py — unique compute_retry test
# ---------------------------------------------------------------------------


class TestComputeRetryCompletedPath:
    """_compute_retry unique test: COMPLETED termination + unparseable."""

    def test_unparseable_on_completed_returns_resume(self):
        s = ClaudeSessionResult(
            subtype="unparseable", is_error=True, result="garbled", session_id=""
        )
        needs, reason = _compute_retry(s, -15, TerminationReason.COMPLETED)
        assert needs is True
        assert reason == RetryReason.RESUME


# ---------------------------------------------------------------------------
# Merged from test_session_result.py — unique extract_token_usage test
# ---------------------------------------------------------------------------


class TestExtractTokenUsageMalformedInput:
    def test_skips_malformed_lines(self):
        malformed = "not json\n" + _assistant_ndjson(input_tokens=10, output_tokens=5)
        result = extract_token_usage(malformed)
        assert result is not None
        assert result["input_tokens"] == 10


# ---------------------------------------------------------------------------
# Merged from test_session_result.py — exhaustiveness guards
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Merged from test_session_result.py — SkillResult
# ---------------------------------------------------------------------------


class TestSkillResult:
    def _make(self, **overrides) -> SkillResult:
        defaults = dict(
            success=True,
            result="done",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
            token_usage=None,
        )
        return SkillResult(**{**defaults, **overrides})

    def test_to_json_produces_all_required_keys(self):
        sr = self._make()
        parsed = json.loads(sr.to_json())
        expected = {
            "success",
            "result",
            "session_id",
            "subtype",
            "is_error",
            "exit_code",
            "needs_retry",
            "retry_reason",
            "stderr",
            "token_usage",
        }
        assert set(parsed.keys()) == expected

    def test_to_json_retry_reason_serializes_as_string(self):
        sr = self._make(needs_retry=True, retry_reason=RetryReason.RESUME)
        parsed = json.loads(sr.to_json())
        assert parsed["retry_reason"] == "resume"

    def test_to_json_preserves_token_usage_dict(self):
        sr = self._make(token_usage={"input_tokens": 10, "model_breakdown": {}})
        result = sr.to_json()
        parsed = json.loads(result)
        assert parsed["token_usage"]["input_tokens"] == 10

    def test_to_json_none_retry_reason_serializes_as_string(self):
        sr = self._make(retry_reason=RetryReason.NONE)
        parsed = json.loads(sr.to_json())
        assert parsed["retry_reason"] == "none"

    def test_to_json_token_usage_none(self):
        sr = self._make(token_usage=None)
        parsed = json.loads(sr.to_json())
        assert parsed["token_usage"] is None


class TestAdjudicationConsistency:
    """Document what _compute_success and _compute_retry produce at the raw function level.

    These tests assert the actual outputs of the individual functions, including
    the known impossible states (dead end, contradiction). Composition guards that
    enforce the valid state space operate at the _build_skill_result boundary
    (tested in TestAdjudicationGuards in test_process_lifecycle.py).

    These tests pass both before and after the fix because the individual functions
    are not changed — only the composition boundary in _build_skill_result is patched.
    """

    @pytest.mark.parametrize(
        "termination,channel,result_content,returncode,subtype,is_error,expected_success,expected_retry",
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
        )
        needs_retry, _ = _compute_retry(
            session,
            returncode,
            termination,
            channel_confirmation=channel,
        )
        assert success == expected_success
        assert needs_retry == expected_retry

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
