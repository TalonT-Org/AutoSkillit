from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from autoskillit.core import BackendEventKind, CodexEventData, ResultParser, SessionEvent
from autoskillit.execution.backends.codex import (
    CodexResultParser,
    _scan_codex_ndjson,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _thread_started_line(thread_id: str) -> str:
    return json.dumps({"type": "thread.started", "thread_id": thread_id})


def _turn_completed_line(usage: Mapping[str, object]) -> str:
    return json.dumps({"type": "turn.completed", "usage": usage})


def _turn_failed_line(error_message: str) -> str:
    return json.dumps({"type": "turn.failed", "error": {"message": error_message}})


def _item_completed_message_line(text: str) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "message", "content": [{"type": "text", "text": text}]},
        }
    )


def _item_completed_function_call_line(name: str, args: str) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "function_call", "name": name, "args": args},
        }
    )


def _item_completed_mcp_tool_call_line(tool_name: str) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "mcp_tool_call", "tool_name": tool_name},
        }
    )


def _item_completed_file_change_line(path: str) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "file_change", "path": path},
        }
    )


class TestScanCodexNdjson:
    def test_scan_empty_stdout_returns_empty_accumulator(self) -> None:
        acc = _scan_codex_ndjson("")
        assert acc.session_id == ""
        assert acc.agent_messages == []
        assert acc.command_executions == []
        assert acc.mcp_tool_calls == []
        assert acc.file_changes == []
        assert acc.last_usage is None
        assert acc.success is False
        assert acc.error_message == ""

    def test_scan_thread_started_populates_session_id(self) -> None:
        acc = _scan_codex_ndjson(_thread_started_line("t1"))
        assert acc.session_id == "t1"

    def test_scan_item_completed_message_appends_agent_messages(self) -> None:
        acc = _scan_codex_ndjson(_item_completed_message_line("hello world"))
        assert acc.agent_messages == ["hello world"]

    def test_scan_item_completed_function_call_appends_command_executions(self) -> None:
        line = _item_completed_function_call_line("Bash", '{"command": "ls"}')
        acc = _scan_codex_ndjson(line)
        assert len(acc.command_executions) == 1
        assert acc.command_executions[0]["name"] == "Bash"

    def test_scan_item_completed_mcp_tool_call_appends_mcp_tool_calls(self) -> None:
        line = _item_completed_mcp_tool_call_line("mcp_tool_name")
        acc = _scan_codex_ndjson(line)
        assert len(acc.mcp_tool_calls) == 1
        assert acc.mcp_tool_calls[0]["tool_name"] == "mcp_tool_name"

    def test_scan_item_completed_file_change_appends_file_changes(self) -> None:
        line = _item_completed_file_change_line("/src/main.py")
        acc = _scan_codex_ndjson(line)
        assert acc.file_changes == ["/src/main.py"]

    def test_scan_turn_completed_stores_last_usage_and_sets_success(self) -> None:
        usage = {"input_tokens": 100, "output_tokens": 50}
        acc = _scan_codex_ndjson(_turn_completed_line(usage))
        assert acc.last_usage == usage
        assert acc.success is True

    def test_scan_turn_failed_marks_error(self) -> None:
        acc = _scan_codex_ndjson(_turn_failed_line("something went wrong"))
        assert acc.error_message == "something went wrong"
        assert acc.success is False

    def test_scan_invalid_json_lines_skipped(self) -> None:
        acc = _scan_codex_ndjson("not json\nalso not json")
        assert acc.session_id == ""
        assert acc.success is False

    def test_scan_non_dict_json_skipped(self) -> None:
        acc = _scan_codex_ndjson("42\n[1,2]")
        assert acc.session_id == ""
        assert acc.success is False

    def test_scan_multiple_turns_last_usage_wins(self) -> None:
        ndjson = (
            _turn_completed_line({"input_tokens": 1, "output_tokens": 1})
            + "\n"
            + _turn_completed_line({"input_tokens": 100, "output_tokens": 50})
        )
        acc = _scan_codex_ndjson(ndjson)
        assert acc.last_usage == {"input_tokens": 100, "output_tokens": 50}

    def test_scan_turn_failed_after_completed_overrides_success(self) -> None:
        ndjson = (
            _turn_completed_line({"input_tokens": 1, "output_tokens": 1})
            + "\n"
            + _turn_failed_line("error after success")
        )
        acc = _scan_codex_ndjson(ndjson)
        assert acc.success is False
        assert acc.error_message == "error after success"


class TestCodexResultParserStdout:
    def test_parse_stdout_empty_returns_error(self) -> None:
        parser = CodexResultParser()
        result = parser.parse_stdout("")
        assert result.raw["subtype"] == "empty_output"
        assert result.raw["is_error"] is True
        assert result.success is False

    def test_parse_stdout_turn_completed_returns_success(self) -> None:
        ndjson = _turn_completed_line({"input_tokens": 100, "output_tokens": 50})
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson)
        assert result.success is True
        assert result.raw["subtype"] == "success"
        assert result.raw["canonical_token_usage"] is not None

    def test_parse_stdout_turn_failed_returns_error_during_execution(self) -> None:
        ndjson = _turn_failed_line("something broke")
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson)
        assert result.raw["subtype"] == "error_during_execution"
        assert result.success is False

    def test_parse_stdout_unparseable_returns_unparseable(self) -> None:
        parser = CodexResultParser()
        result = parser.parse_stdout("garbage\nmore garbage")
        assert result.raw["subtype"] == "unparseable"
        assert result.success is False

    def test_parse_stdout_exit_code_forwarded(self) -> None:
        ndjson = _turn_completed_line({"input_tokens": 1, "output_tokens": 1})
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson, exit_code=42)
        assert result.exit_code == 42

    def test_parse_stdout_exit_code_defaulted_on_error(self) -> None:
        parser = CodexResultParser()
        result = parser.parse_stdout("", exit_code=0)
        assert result.exit_code == 1

    def test_parse_stdout_token_usage_via_canonical(self) -> None:
        ndjson = _turn_completed_line(
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_input_tokens": 25,
            }
        )
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson)
        canonical = result.raw["canonical_token_usage"]
        assert canonical["input_tokens"] == 100
        assert canonical["output_tokens"] == 50
        assert canonical["cache_read_tokens"] == 25
        assert canonical["provider"] == "codex"

    def test_parse_stdout_session_id_from_thread_started(self) -> None:
        ndjson = _thread_started_line("my-session-id")
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson)
        assert result.session_id == "my-session-id"

    def test_parse_stdout_output_joins_agent_messages(self) -> None:
        ndjson = (
            _item_completed_message_line("line one")
            + "\n"
            + _item_completed_message_line("line two")
        )
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson)
        assert result.output == "line one\nline two"

    def test_parse_stdout_raw_contains_all_fields(self) -> None:
        ndjson = (
            _thread_started_line("s1")
            + "\n"
            + _item_completed_message_line("hello")
            + "\n"
            + _item_completed_function_call_line("Bash", "{}")
            + "\n"
            + _item_completed_mcp_tool_call_line("mcp_tool")
            + "\n"
            + _item_completed_file_change_line("/path/file.py")
            + "\n"
            + _turn_completed_line({"input_tokens": 1, "output_tokens": 1})
        )
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson)
        raw = result.raw
        assert raw["subtype"] == "success"
        assert raw["is_error"] is False
        assert raw["token_usage"] == {"input_tokens": 1, "output_tokens": 1}
        assert raw["canonical_token_usage"] is not None
        assert raw["agent_messages"] == ["hello"]
        assert len(raw["command_executions"]) == 1
        assert raw["command_executions"][0]["name"] == "Bash"
        assert len(raw["mcp_tool_calls"]) == 1
        assert raw["mcp_tool_calls"][0]["tool_name"] == "mcp_tool"
        assert raw["file_changes"] == ["/path/file.py"]


class TestCodexResultParserEvents:
    def test_parse_result_completion_events_yield_success(self) -> None:
        parser = CodexResultParser()
        events = [
            SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=True,
                backend_data=CodexEventData(
                    record_type="result",
                    thread_id="t1",
                    item_type="completion",
                    raw={},
                ),
            ),
        ]
        result = parser.parse_result(events)
        assert result.success is True

    def test_parse_result_empty_events_returns_error(self) -> None:
        parser = CodexResultParser()
        result = parser.parse_result([])
        assert result.success is False
        assert result.exit_code == 1
        assert result.error == "empty events sequence"

    def test_parse_result_no_completion_yields_failure(self) -> None:
        parser = CodexResultParser()
        events = [
            SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id="codex-s1",
            ),
        ]
        result = parser.parse_result(events)
        assert result.success is False

    def test_parse_result_extracts_session_id_from_meta(self) -> None:
        parser = CodexResultParser()
        events = [
            SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id="codex-session-abc",
            ),
        ]
        result = parser.parse_result(events)
        assert result.session_id == "codex-session-abc"


class TestCodexResultParserConformance:
    def test_structural_conformance_result_parser(self) -> None:
        assert isinstance(CodexResultParser(), ResultParser)
