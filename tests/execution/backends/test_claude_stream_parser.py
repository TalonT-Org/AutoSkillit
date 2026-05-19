from __future__ import annotations

import pytest

from autoskillit.core import BackendEventKind, StreamParser
from autoskillit.execution.backends import ClaudeStreamParser

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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
