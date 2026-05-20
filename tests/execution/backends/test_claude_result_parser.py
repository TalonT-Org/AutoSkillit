from __future__ import annotations

from unittest.mock import patch

import pytest

from autoskillit.core import BackendEventKind, ClaudeEventData, ResultParser, SessionEvent
from autoskillit.execution.backends import ClaudeResultParser
from autoskillit.execution.session import ClaudeSessionResult, CliSubtype

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_completion_event(
    *,
    has_marker: bool = True,
    result: str = "done",
    subtype: str = "success",
    session_id: str = "s1",
) -> SessionEvent:
    return SessionEvent(
        kind=BackendEventKind.COMPLETION,
        is_terminal=True,
        has_marker=has_marker,
        backend_data=ClaudeEventData(
            record_type="result",
            subtype=subtype,
            session_id=session_id,
            raw={"result": result, "subtype": subtype},
        ),
    )


def _make_meta_event(*, session_id: str = "s1") -> SessionEvent:
    return SessionEvent(
        kind=BackendEventKind.SESSION_META,
        is_terminal=False,
        has_marker=False,
        session_id=session_id,
    )


class TestClaudeResultParser:
    def test_parse_result_success_from_events(self) -> None:
        parser = ClaudeResultParser()
        events = [
            SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id="test-session",
            ),
            SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=True,
                backend_data=ClaudeEventData(
                    record_type="result",
                    subtype="success",
                    session_id="test-session",
                    raw={"result": "done"},
                ),
            ),
        ]
        result = parser.parse_result(events)
        assert result.success is True
        assert result.session_id == "test-session"
        assert result.output == "done"

    def test_parse_result_failure_no_completion(self) -> None:
        parser = ClaudeResultParser()
        events = [
            SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id="test-session",
            ),
        ]
        result = parser.parse_result(events)
        assert result.success is False

    def test_parse_result_failure_no_marker(self) -> None:
        parser = ClaudeResultParser()
        events = [
            SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=False,
                backend_data=ClaudeEventData(
                    record_type="result",
                    subtype="success",
                    session_id="test-session",
                    raw={"result": "done"},
                ),
            ),
        ]
        result = parser.parse_result(events)
        assert result.success is False

    def test_parse_result_extracts_session_id(self) -> None:
        parser = ClaudeResultParser()
        events = [
            SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id="session-abc",
            ),
        ]
        result = parser.parse_result(events)
        assert result.session_id == "session-abc"

    def test_parse_result_extracts_output(self) -> None:
        parser = ClaudeResultParser()
        events = [
            SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=True,
                backend_data=ClaudeEventData(
                    record_type="result",
                    subtype="success",
                    session_id="",
                    raw={"result": "task completed"},
                ),
            ),
        ]
        result = parser.parse_result(events)
        assert result.output == "task completed"

    def test_parse_stdout_maps_all_fields(self) -> None:
        mock_result = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="hello world",
            session_id="sess-123",
            errors=[],
            token_usage={"input_tokens": 100, "output_tokens": 50},
            assistant_messages=['{"type": "assistant", "message": {}}'],
            tool_uses=[{"name": "Write", "id": "1", "file_path": "/tmp/foo.py"}],
            jsonl_context_exhausted=False,
            stop_reasons=["end_turn"],
            has_thinking_only_turn=False,
            seen_block_types=frozenset({"text", "tool_use"}),
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ):
            parser = ClaudeResultParser()
            result = parser.parse_stdout('{"type": "result"}')
            assert result.success is True
            assert result.session_id == "sess-123"
            assert result.output == "hello world"
            assert result.backend_name == "claude-code"
            raw = dict(result.raw)
            assert raw["subtype"] == "success"
            assert raw["is_error"] is False
            assert raw["token_usage"] == {"input_tokens": 100, "output_tokens": 50}
            assert raw["write_artifacts"] == ["/tmp/foo.py"]
            assert raw["tool_uses"] == [{"name": "Write", "id": "1", "file_path": "/tmp/foo.py"}]
            assert raw["assistant_messages"] == ['{"type": "assistant", "message": {}}']
            assert raw["jsonl_context_exhausted"] is False
            assert raw["stop_reasons"] == ["end_turn"]
            assert raw["has_thinking_only_turn"] is False
            assert set(raw["seen_block_types"]) == {"text", "tool_use"}

    def test_parse_stdout_empty_output_error(self) -> None:
        mock_result = ClaudeSessionResult(
            subtype=CliSubtype.EMPTY_OUTPUT,
            is_error=True,
            result="",
            session_id="",
            errors=["empty output"],
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ):
            parser = ClaudeResultParser()
            result = parser.parse_stdout("")
            assert result.success is False
            assert result.error == "empty output"
            raw = dict(result.raw)
            assert raw["subtype"] == "empty_output"
            assert raw["is_error"] is True

    def test_parse_stdout_write_artifacts_in_raw(self) -> None:
        mock_result = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="",
            errors=[],
            tool_uses=[
                {"name": "Write", "id": "1", "file_path": "/a/b.py"},
                {"name": "Bash", "id": "2", "bash_paths": ["/x.sh"]},
                {"name": "Edit", "id": "3", "file_path": "/c/d.py"},
            ],
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ):
            parser = ClaudeResultParser()
            result = parser.parse_stdout('{"type": "result"}')
            assert result.raw["write_artifacts"] == ["/a/b.py", "/c/d.py"]

    def test_parse_stdout_no_double_token_extraction(self) -> None:
        mock_result = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="",
            errors=[],
            token_usage={"input_tokens": 10},
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ) as mock_parse:
            parser = ClaudeResultParser()
            parser.parse_stdout('{"type": "result"}')
            mock_parse.assert_called_once()

    def test_parse_stdout_seen_block_types_is_list(self) -> None:
        mock_result = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="",
            seen_block_types=frozenset({"text", "thinking"}),
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ):
            parser = ClaudeResultParser()
            result = parser.parse_stdout('{"type": "result"}')
            assert isinstance(result.raw["seen_block_types"], list)
            assert set(result.raw["seen_block_types"]) == {"text", "thinking"}

    def test_extract_write_artifacts_filters_correctly(self) -> None:
        from autoskillit.execution.backends.claude import _extract_write_artifacts

        tool_uses = [
            {"name": "Write", "id": "1", "file_path": "/a.py"},
            {"name": "Bash", "id": "2"},
            {
                "name": "Edit",
                "id": "3",
                "file_path": "/b.py",
            },  # Edit modifies files — counts as write artifact
            {"name": "Write", "id": "4"},  # no file_path — silently skipped
            {"name": "Read", "id": "5", "file_path": "/c.py"},
        ]
        assert _extract_write_artifacts(tool_uses) == ["/a.py", "/b.py"]

    def test_extract_write_artifacts_empty_list(self) -> None:
        from autoskillit.execution.backends.claude import _extract_write_artifacts

        assert _extract_write_artifacts([]) == []

    def test_structural_conformance_result_parser(self) -> None:
        assert isinstance(ClaudeResultParser(), ResultParser)


class TestClaudeResultParserTokenExtraction:
    def test_token_usage_dict_preserved(self) -> None:
        mock_result = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="",
            errors=[],
            token_usage={"input_tokens": 500, "output_tokens": 200},
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ):
            parser = ClaudeResultParser()
            result = parser.parse_stdout('{"type": "result"}')
            assert result.raw["token_usage"] == {"input_tokens": 500, "output_tokens": 200}

    def test_token_usage_none_preserved(self) -> None:
        mock_result = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="",
            errors=[],
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ):
            parser = ClaudeResultParser()
            result = parser.parse_stdout('{"type": "result"}')
            assert result.raw["token_usage"] is None

    def test_token_usage_empty_dict(self) -> None:
        mock_result = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="",
            errors=[],
            token_usage={},
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ):
            parser = ClaudeResultParser()
            result = parser.parse_stdout('{"type": "result"}')
            assert result.raw["token_usage"] == {}

    def test_token_usage_with_cache_fields(self) -> None:
        mock_result = ClaudeSessionResult(
            subtype=CliSubtype.SUCCESS,
            is_error=False,
            result="done",
            session_id="",
            errors=[],
            token_usage={
                "input_tokens": 100,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 25,
            },
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ):
            parser = ClaudeResultParser()
            result = parser.parse_stdout('{"type": "result"}')
            raw_tu = result.raw["token_usage"]
            assert "input_tokens" in raw_tu
            assert "cache_creation_input_tokens" in raw_tu
            assert "cache_read_input_tokens" in raw_tu


class TestClaudeResultParserMarkerDetection:
    def test_standalone_marker_yields_success(self) -> None:
        parser = ClaudeResultParser()
        events = [_make_completion_event(has_marker=True)]
        result = parser.parse_result(events)
        assert result.success is True

    def test_embedded_marker_absent_yields_failure(self) -> None:
        parser = ClaudeResultParser()
        events = [_make_completion_event(has_marker=False)]
        result = parser.parse_result(events)
        assert result.success is False

    def test_no_completion_event_yields_failure(self) -> None:
        parser = ClaudeResultParser()
        events = [_make_meta_event()]
        result = parser.parse_result(events)
        assert result.success is False

    def test_any_completion_marker_yields_success(self) -> None:
        parser = ClaudeResultParser()
        events = [
            _make_completion_event(has_marker=False),
            _make_completion_event(has_marker=True),
        ]
        result = parser.parse_result(events)
        assert result.success is True


class TestCliSubtypeRoundTrip:
    @pytest.mark.parametrize("subtype", list(CliSubtype))
    def test_round_trip_through_parse_stdout(self, subtype: CliSubtype) -> None:
        mock_result = ClaudeSessionResult(
            subtype=subtype,
            is_error=False,
            result="done",
            session_id="",
            errors=[],
        )
        with patch(
            "autoskillit.execution.backends.claude.parse_session_result",
            return_value=mock_result,
        ):
            parser = ClaudeResultParser()
            result = parser.parse_stdout('{"type": "result"}')
            raw = dict(result.raw)
            assert raw["subtype"] == subtype.value
            assert CliSubtype.from_cli(raw["subtype"]) == subtype
