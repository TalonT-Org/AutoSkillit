"""L1 unit tests for ClaudeSessionResult and parse_session_result — result types and policies."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import (
    CONTEXT_EXHAUSTION_MARKER,
    RetryReason,
)
from autoskillit.execution.session import ClaudeSessionResult, parse_session_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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
            "result": CONTEXT_EXHAUSTION_MARKER.capitalize(),
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
        """Phase 1 (ClaudeSessionResult.retry_reason property) always returns RESUME
        when needs_retry is True.

        This property is only triggered by context/turn limit signals (error_max_turns
        and context_exhaustion). Both conditions always have partial progress on disk
        and always return RESUME.

        Note: _compute_retry() can return RetryReason.EMPTY_OUTPUT for NATURAL_EXIT +
        kill_anomaly cases with no context exhaustion. That is Phase 2 behavior and is
        not tested here.
        """
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


class TestClaudeSessionResultBasic:
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

    def test_jsonl_flat_record_sets_context_exhausted_flag(self):
        """parse_session_result sets jsonl_context_exhausted=True for flat assistant record."""
        import json

        flat_record = json.dumps(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "Prompt is too long"}],
                "output_tokens": 0,
                "input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        )
        result_record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "sess-1",
            }
        )
        session = parse_session_result(flat_record + "\n" + result_record)
        assert session.jsonl_context_exhausted is True

    def test_jsonl_flat_record_is_context_exhausted_bypasses_is_error_guard(self):
        """_is_context_exhausted() returns True via JSONL flag even when is_error=False."""
        s = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="",
            session_id="s1",
            jsonl_context_exhausted=True,
        )
        assert s._is_context_exhausted() is True
        assert s.needs_retry is True

    def test_jsonl_flat_record_nonzero_output_tokens_not_detected(self):
        """Flat assistant record with output_tokens > 0 does NOT trigger detection.

        The content deliberately contains CONTEXT_EXHAUSTION_MARKER so that only
        the output_tokens != 0 guard suppresses detection — isolating the gate
        under test.
        """
        flat_record = json.dumps(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": CONTEXT_EXHAUSTION_MARKER}],
                "output_tokens": 42,
                "input_tokens": 100,
            }
        )
        result_record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done %%ORDER_UP%%",
                "session_id": "sess-2",
            }
        )
        session = parse_session_result(flat_record + "\n" + result_record)
        assert session.jsonl_context_exhausted is False


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
# CliSubtype sealed enum tests
# ---------------------------------------------------------------------------


class TestCliSubtypeExhaustiveCoverage:
    """Every subtype returned by parse_session_result is a CliSubtype member."""

    def test_success_subtype_is_cli_subtype(self):
        from autoskillit.core.types import CliSubtype

        ndjson = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "ok",
                "session_id": "s1",
            }
        )
        session = parse_session_result(ndjson)
        assert isinstance(session.subtype, CliSubtype)
        assert session.subtype == CliSubtype.SUCCESS

    def test_empty_output_subtype_is_cli_subtype(self):
        from autoskillit.core.types import CliSubtype

        session = parse_session_result("")
        assert isinstance(session.subtype, CliSubtype)
        assert session.subtype == CliSubtype.EMPTY_OUTPUT

    def test_unparseable_subtype_is_cli_subtype(self):
        from autoskillit.core.types import CliSubtype

        session = parse_session_result("not json at all")
        assert isinstance(session.subtype, CliSubtype)
        assert session.subtype == CliSubtype.UNPARSEABLE

    def test_unknown_cli_subtype_maps_to_unknown(self):
        from autoskillit.core.types import CliSubtype

        ndjson = json.dumps(
            {
                "type": "result",
                "subtype": "some_future_subtype",
                "is_error": False,
                "result": "ok",
                "session_id": "s1",
            }
        )
        session = parse_session_result(ndjson)
        assert isinstance(session.subtype, CliSubtype)
        assert session.subtype == CliSubtype.UNKNOWN

    @pytest.mark.parametrize(
        "raw_subtype",
        [
            "success",
            "error_max_turns",
            "error_during_execution",
            "unknown",
            "empty_output",
            "unparseable",
            "timeout",
        ],
    )
    def test_all_known_subtypes_are_cli_subtype_members(self, raw_subtype: str):
        from autoskillit.core.types import CliSubtype

        ndjson = json.dumps(
            {
                "type": "result",
                "subtype": raw_subtype,
                "is_error": False,
                "result": "ok",
                "session_id": "s1",
            }
        )
        session = parse_session_result(ndjson)
        assert isinstance(session.subtype, CliSubtype)

    def test_string_subtype_coerced_in_post_init(self):
        from autoskillit.core.types import CliSubtype

        session = ClaudeSessionResult(
            subtype="success",  # type: ignore[arg-type]
            is_error=False,
            result="ok",
            session_id="s1",
        )
        assert isinstance(session.subtype, CliSubtype)
        assert session.subtype == CliSubtype.SUCCESS


class TestLifespanStarted:
    def test_lifespan_started_true_when_tool_uses_present(self):
        from autoskillit.execution.session import ClaudeSessionResult

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="ok",
            session_id="s1",
            tool_uses=[{"name": "open_kitchen", "id": "t1"}],
        )
        assert session.lifespan_started is True

    def test_lifespan_started_false_when_no_tool_uses(self):
        from autoskillit.execution.session import ClaudeSessionResult

        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="ok",
            session_id="s1",
            tool_uses=[],
        )
        assert session.lifespan_started is False


# ---------------------------------------------------------------------------
# stop_reasons extraction tests
# ---------------------------------------------------------------------------


def test_parse_session_result_extracts_stop_reasons():
    ndjson = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"stop_reason": "tool_use", "content": [], "usage": {}},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"stop_reason": "end_turn", "content": [], "usage": {}},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "Done.",
                    "session_id": "abc",
                }
            ),
        ]
    )
    parsed = parse_session_result(ndjson)
    assert parsed.stop_reasons == ["tool_use", "end_turn"]
    assert parsed.last_stop_reason == "end_turn"


def test_parse_session_result_empty_stop_reasons_when_absent():
    ndjson = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Done.",
            "session_id": "abc",
        }
    )
    parsed = parse_session_result(ndjson)
    assert parsed.stop_reasons == []
    assert parsed.last_stop_reason == ""


def test_last_stop_reason_is_last_turn():
    ndjson = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "requestId": "r1",
                    "timestamp": "t1",
                    "message": {"stop_reason": "tool_use", "content": [], "usage": {}},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "requestId": "r2",
                    "timestamp": "t2",
                    "message": {"stop_reason": "max_tokens", "content": [], "usage": {}},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "x",
                    "session_id": "s",
                }
            ),
        ]
    )
    parsed = parse_session_result(ndjson)
    assert parsed.last_stop_reason == "max_tokens"


def test_parse_session_result_missing_stop_reason_skipped():
    ndjson = "\n".join(
        [
            json.dumps({"type": "assistant", "message": {"content": [], "usage": {}}}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "ok",
                    "session_id": "s",
                }
            ),
        ]
    )
    parsed = parse_session_result(ndjson)
    assert parsed.stop_reasons == []
    assert parsed.last_stop_reason == ""
