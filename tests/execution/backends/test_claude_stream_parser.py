from __future__ import annotations

import json
from typing import Any

import pytest

from autoskillit.core import BackendEventKind, ClaudeEventData, StreamParser
from autoskillit.execution.backends import ClaudeStreamParser

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _result_line(*, result: str = "done", subtype: str = "success", session_id: str = "s1") -> str:
    return json.dumps(
        {"type": "result", "result": result, "subtype": subtype, "session_id": session_id}
    )


def _system_line(*, session_id: str = "test-session") -> str:
    return json.dumps({"type": "system", "session_id": session_id})


def _assistant_line(**overrides: Any) -> str:
    base: dict[str, Any] = {"type": "assistant", "message": {"content": "hello"}}
    base.update(overrides)
    return json.dumps(base)


def _context_exhaustion_line() -> str:
    return json.dumps(
        {
            "type": "assistant",
            "output_tokens": 0,
            "content": [{"type": "text", "text": "prompt is too long"}],
        }
    )


class TestClaudeStreamParser:
    def test_parse_line_system_record_session_meta(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "system", "session_id": "test-session-123"}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.SESSION_META
        assert result.session_id == "test-session-123"

    def test_parse_line_result_with_marker(self) -> None:
        parser = ClaudeStreamParser(completion_marker="<result>")
        line = '{"type": "result", "result": "<result>", "subtype": "success", "session_id": "s1"}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.COMPLETION
        assert result.has_marker is True

    def test_parse_line_result_without_marker(self) -> None:
        parser = ClaudeStreamParser(completion_marker="<result>")
        line = (
            '{"type": "result", "result": "some text", "subtype": "success", "session_id": "s1"}'
        )
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.COMPLETION
        assert result.has_marker is False

    def test_parse_line_result_empty_ignored(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "result", "result": ""}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.IGNORED

    def test_parse_line_assistant_context_exhaustion(self) -> None:
        parser = ClaudeStreamParser()
        line = (
            '{"type": "assistant", "output_tokens": 0,'
            ' "content": [{"type": "text", "text": "prompt is too long"}]}'
        )
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.TOOL_OUTPUT

    def test_parse_line_assistant_with_message_key_and_zero_tokens_ignored(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "assistant", "message": {"content": "hello"}, "output_tokens": 0}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.IGNORED

    def test_parse_line_regular_assistant_ignored(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "assistant", "message": {"content": "hello"}}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.IGNORED

    def test_parse_line_unknown_type_ignored(self) -> None:
        parser = ClaudeStreamParser()
        line = '{"type": "user", "content": "hello"}'
        result = parser.parse_line(line)
        assert result is not None
        assert result.kind == BackendEventKind.IGNORED

    def test_parse_line_malformed_json_none(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line("not valid json")
        assert result is None

    def test_parse_line_empty_string_none(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line("")
        assert result is None

    def test_parse_line_non_dict_json_none(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line("123")
        assert result is None

    def test_structural_conformance_stream_parser(self) -> None:
        assert isinstance(ClaudeStreamParser(), StreamParser)


class TestClaudeStreamParserBackendData:
    def test_result_line_populates_claude_event_data(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_result_line())
        assert result is not None
        assert result.backend_data is not None
        assert isinstance(result.backend_data, ClaudeEventData)
        assert result.backend_data.record_type == "result"
        assert result.backend_data.subtype == "success"
        assert result.backend_data.session_id == "s1"

    def test_result_line_raw_contains_original_dict(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_result_line())
        assert result is not None
        assert result.backend_data is not None
        assert result.backend_data.raw["type"] == "result"
        assert result.backend_data.raw["result"] == "done"

    def test_context_exhaustion_populates_backend_data(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_context_exhaustion_line())
        assert result is not None
        assert result.backend_data is not None
        assert result.backend_data.subtype == "context_exhaustion"
        assert result.backend_data.record_type == "assistant"

    def test_system_line_has_no_backend_data(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_system_line())
        assert result is not None
        assert result.backend_data is None

    def test_ignored_line_has_no_backend_data(self) -> None:
        parser = ClaudeStreamParser()
        result = parser.parse_line(_assistant_line())
        assert result is not None
        assert result.backend_data is None
