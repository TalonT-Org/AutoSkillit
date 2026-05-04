"""L1 unit tests for execution/session.py — token extraction, parsing, and SkillResult."""

from __future__ import annotations

import json

import pytest
import structlog

from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.session import (
    SkillResult,
    extract_token_usage,
    parse_session_result,
)
from tests._helpers import _flush_structlog_proxy_caches as _flush_logger_proxy_caches

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_session_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    termination_reason: TerminationReason = TerminationReason.NATURAL_EXIT,
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
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
            "cli_subtype",
            "is_error",
            "exit_code",
            "kill_reason",
            "last_stop_reason",
            "lifespan_started",
            "provider_fallback",
            "needs_retry",
            "retry_reason",
            "order_id",
            "stderr",
            "token_usage",
            "write_path_warnings",
            "write_call_count",
            "fs_writes_detected",
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


# ---------------------------------------------------------------------------
# Accumulator pattern tests: state preserved on fallback paths
# ---------------------------------------------------------------------------


class TestAccumulatorPreservesState:
    """Ensure tool_uses, assistant_messages, and token_usage survive fallback paths."""

    @staticmethod
    def _tool_use_ndjson(*tool_names: str) -> str:
        """Build NDJSON with tool_use blocks but NO type=result record."""
        lines = []
        for name in tool_names:
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "tool_use", "name": name, "id": f"tu_{name}"},
                                {"type": "text", "text": f"using {name}"},
                            ],
                            "usage": {"input_tokens": 100, "output_tokens": 50},
                            "model": "claude-test",
                        },
                    }
                )
            )
        return "\n".join(lines)

    def test_tool_uses_survive_fallback_path(self):
        """tool_uses must be populated even when no type=result record exists."""
        ndjson = self._tool_use_ndjson("Write", "Edit")
        session = parse_session_result(ndjson)
        assert session.subtype == "unparseable"
        assert len(session.tool_uses) == 2
        assert session.tool_uses[0]["name"] == "Write"
        assert session.tool_uses[1]["name"] == "Edit"

    def test_assistant_messages_survive_fallback_path(self):
        """assistant_messages must be populated even when no type=result record exists."""
        ndjson = self._tool_use_ndjson("Write")
        session = parse_session_result(ndjson)
        assert len(session.assistant_messages) > 0

    def test_token_usage_survives_fallback_path(self):
        """token_usage must be populated even when no type=result record exists."""
        ndjson = self._tool_use_ndjson("Write")
        session = parse_session_result(ndjson)
        assert session.token_usage is not None

    def test_context_exhaustion_via_flat_record_without_result(self):
        """Flat context-exhaustion record sets CONTEXT_EXHAUSTION subtype."""
        from autoskillit.core.types import CliSubtype

        # Standard assistant record with tool uses
        assistant = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Write", "id": "tu_1"},
                        {"type": "text", "text": "writing file"},
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                    "model": "claude-test",
                },
            }
        )
        # Flat context exhaustion record (no "message" key, zero output tokens)
        flat_exhaust = json.dumps(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "prompt is too long"}],
                "output_tokens": 0,
                "input_tokens": 0,
            }
        )
        ndjson = assistant + "\n" + flat_exhaust
        session = parse_session_result(ndjson)
        assert session.subtype == CliSubtype.CONTEXT_EXHAUSTION
        assert session.jsonl_context_exhausted is True
        assert len(session.tool_uses) == 1


# ---------------------------------------------------------------------------
# Integration tests: full chain verification
# ---------------------------------------------------------------------------


class TestFullChainZeroWriteGate:
    """Full chain: NDJSON → parse_session_result → write_call_count."""

    def test_write_call_count_survives_unparseable(self):
        """write_call_count must see writes even when session is unparseable."""
        ndjson = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "tool_use", "name": "Edit", "id": "tu_1"},
                                {"type": "tool_use", "name": "Write", "id": "tu_2"},
                            ],
                        },
                    }
                ),
            ]
        )
        session = parse_session_result(ndjson)
        write_call_count = sum(1 for t in session.tool_uses if t.get("name") in {"Write", "Edit"})
        assert write_call_count == 2
