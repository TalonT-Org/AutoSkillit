"""Tests for autoskillit.session_parser module."""

from __future__ import annotations

import json

from autoskillit.session_parser import (
    ClaudeSessionResult,
    extract_token_usage,
    parse_session_result,
)
from autoskillit.types import CONTEXT_EXHAUSTION_MARKER, RetryReason


class TestSessionParserModuleExists:
    def test_claudesessionresult_importable(self):
        assert ClaudeSessionResult is not None

    def test_parse_session_result_importable(self):
        assert callable(parse_session_result)

    def test_extract_token_usage_importable(self):
        assert callable(extract_token_usage)


class TestFromResultDict:
    def test_basic_construction(self):
        obj = {"subtype": "success", "is_error": False, "result": "ok", "session_id": "abc"}
        r = ClaudeSessionResult.from_result_dict(obj)
        assert r.subtype == "success"
        assert r.is_error is False
        assert r.result == "ok"
        assert r.session_id == "abc"
        assert r.errors == []
        assert r.token_usage is None

    def test_defaults_for_missing_keys(self):
        r = ClaudeSessionResult.from_result_dict({})
        assert r.subtype == "unknown"
        assert r.is_error is False
        assert r.result == ""
        assert r.session_id == ""

    def test_with_token_usage_kwarg(self):
        usage = {"input_tokens": 10, "output_tokens": 5}
        r = ClaudeSessionResult.from_result_dict(
            {"subtype": "success", "is_error": False, "result": "", "session_id": ""},
            token_usage=usage,
        )
        assert r.token_usage == usage

    def test_parse_session_result_uses_from_result_dict(self):
        line = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                "errors": [],
            }
        )
        r = parse_session_result(line)
        assert r.subtype == "success"
        assert r.result == "done"

    def test_errors_list_preserved(self):
        obj = {
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "",
            "session_id": "s2",
            "errors": ["err1", "err2"],
        }
        r = ClaudeSessionResult.from_result_dict(obj)
        assert r.errors == ["err1", "err2"]

    def test_errors_defaults_to_empty_list(self):
        r = ClaudeSessionResult.from_result_dict({"subtype": "success", "is_error": False})
        assert r.errors == []


class TestClaudeSessionResult:
    def test_parses_success_result(self):
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
        parsed = parse_session_result("Traceback (most recent call last):\n  File...")
        assert parsed.is_error is True
        assert parsed.subtype == "unparseable"
        assert "Traceback" in parsed.result
        assert parsed.needs_retry is False

    def test_empty_stdout_is_error(self):
        parsed = parse_session_result("")
        assert parsed.is_error is True
        assert parsed.subtype == "empty_output"
        assert parsed.result == ""
        assert parsed.needs_retry is False

    def test_whitespace_only_stdout_is_error(self):
        parsed = parse_session_result("  \n  \t  ")
        assert parsed.is_error is True
        assert parsed.subtype == "empty_output"

    def test_json_without_type_result_is_error(self):
        parsed = parse_session_result('{"some": "random", "json": true}')
        assert parsed.is_error is True
        assert parsed.subtype == "unparseable"

    def test_non_dict_json_is_error(self):
        parsed = parse_session_result("[1, 2, 3]")
        assert parsed.is_error is True
        assert parsed.subtype == "unparseable"

    def test_handles_ndjson_with_multiple_lines(self):
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
                assert session.retry_reason == RetryReason.RESUME
            else:
                assert session.retry_reason == RetryReason.NONE

    def test_all_retriable_cases_produce_same_retry_reason(self):
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

    def test_context_exhaustion_via_errors_list(self):
        """Marker in errors list triggers context exhaustion detection."""
        session = ClaudeSessionResult(
            subtype="error_during_execution",
            is_error=True,
            result="some result",
            session_id="s1",
            errors=[f"claude: {CONTEXT_EXHAUSTION_MARKER}"],
        )
        assert session.needs_retry is True
        assert session.retry_reason == RetryReason.RESUME

    def test_agent_result_rewrites_context_exhaustion(self):
        session = ClaudeSessionResult(
            subtype="success",
            is_error=True,
            result="Prompt is too long",
            session_id="s1",
        )
        assert "context limit" in session.agent_result.lower()

    def test_agent_result_rewrites_max_turns(self):
        session = ClaudeSessionResult(
            subtype="error_max_turns",
            is_error=False,
            result="",
            session_id="s1",
        )
        assert "turn limit" in session.agent_result.lower()

    def test_agent_result_passthrough_on_success(self):
        session = ClaudeSessionResult(
            subtype="success",
            is_error=False,
            result="The task is done.",
            session_id="s1",
        )
        assert session.agent_result == "The task is done."


class TestExtractTokenUsage:
    def test_single_assistant_record(self):
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
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 200,
                            "output_tokens": 75,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    },
                }
            ),
        ]
        result = extract_token_usage("\n".join(lines))
        assert result is not None
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 125

    def test_result_record_usage_preferred_for_totals(self):
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "m1",
                        "usage": {
                            "input_tokens": 999,
                            "output_tokens": 999,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 2,
                        "cache_creation_input_tokens": 3,
                        "cache_read_input_tokens": 4,
                    },
                }
            ),
        ]
        result = extract_token_usage("\n".join(lines))
        assert result is not None
        assert result["input_tokens"] == 1
        assert result["output_tokens"] == 2

    def test_no_usage_data_returns_none(self):
        stdout = json.dumps({"type": "system", "message": "hello"})
        result = extract_token_usage(stdout)
        assert result is None

    def test_empty_stdout_returns_none(self):
        assert extract_token_usage("") is None

    def test_non_json_stdout_returns_none(self):
        assert extract_token_usage("not json at all") is None

    def test_cache_tokens_default_to_zero(self):
        stdout = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "m1",
                    "usage": {"input_tokens": 5, "output_tokens": 3},
                },
            }
        )
        result = extract_token_usage(stdout)
        assert result is not None
        assert result["cache_creation_input_tokens"] == 0
        assert result["cache_read_input_tokens"] == 0
