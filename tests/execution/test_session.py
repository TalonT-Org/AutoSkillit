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

    def test_list_format_content_blocks_joined_with_newline(self):
        """List-format content blocks are joined with newline, preserving standalone lines.

        When an assistant record's content is a list of blocks and the marker
        occupies its own block, the resulting joined text preserves the marker
        as a standalone line so _marker_is_standalone returns True.
        """
        ndjson = (
            '{"type":"assistant","message":{"role":"assistant","content":'
            '[{"type":"text","text":"GO verdict."},{"type":"text","text":"%%ORDER_UP%%"}]}}\n'
            '{"type":"result","subtype":"success","result":"","session_id":"s1","is_error":false}\n'
        )
        result = parse_session_result(ndjson)
        assert result.assistant_messages == ["GO verdict.\n%%ORDER_UP%%"]
        # The newline join means _marker_is_standalone correctly detects the marker
        from autoskillit.execution.process import _marker_is_standalone

        assert _marker_is_standalone(result.assistant_messages[0], "%%ORDER_UP%%") is True


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


class TestExtractTokenUsageMalformedInput:
    def test_skips_malformed_lines(self):
        malformed = "not json\n" + _assistant_ndjson(input_tokens=10, output_tokens=5)
        result = extract_token_usage(malformed)
        assert result is not None
        assert result["input_tokens"] == 10


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


