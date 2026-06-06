from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from autoskillit.core import BackendEventKind, CodexEventData, ResultParser, SessionEvent
from autoskillit.execution.backends._codex_parse import (
    CodexResultParser,
    CodexStreamParser,
    _scan_codex_ndjson,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _thread_started_line(thread_id: str) -> str:
    return json.dumps({"type": "thread.started", "thread_id": thread_id})


def _session_meta_line(thread_id: str) -> str:
    return json.dumps({"type": "session_meta", "payload": {"id": thread_id}})


def _turn_completed_line(usage: Mapping[str, object]) -> str:
    return json.dumps({"type": "turn.completed", "usage": usage})


def _turn_failed_line(error_message: str) -> str:
    return json.dumps({"type": "turn.failed", "error": {"message": error_message}})


def _turn_failed_code_line(code: str, message: str) -> str:
    return json.dumps({"type": "turn.failed", "error": {"message": message, "code": code}})


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


def _item_started_function_call_line(name: str, args: str) -> str:
    return json.dumps(
        {
            "type": "item.started",
            "item": {"type": "function_call", "name": name, "args": args},
        }
    )


def _item_started_file_change_line(path: str) -> str:
    return json.dumps(
        {
            "type": "item.started",
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
        assert acc.error_code == ""

    def test_scan_thread_started_populates_session_id(self) -> None:
        acc = _scan_codex_ndjson(_thread_started_line("t1"))
        assert acc.session_id == "t1"

    def test_scan_session_meta_populates_session_id(self) -> None:
        acc = _scan_codex_ndjson(_session_meta_line("t2"))
        assert acc.session_id == "t2"

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

    def test_scan_turn_completed_after_failed_does_not_override(self) -> None:
        ndjson = (
            _turn_failed_line("first turn failed")
            + "\n"
            + _turn_completed_line({"input_tokens": 1, "output_tokens": 1})
        )
        acc = _scan_codex_ndjson(ndjson)
        assert acc.success is False
        assert acc.saw_failure is True
        assert acc.error_message == "first turn failed"

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

    def test_parse_stdout_exit_code_clamped_to_zero_on_success(self) -> None:
        ndjson = _turn_completed_line({"input_tokens": 1, "output_tokens": 1})
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson, exit_code=42)
        assert result.exit_code == 0

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

    def test_parse_stdout_session_id_from_session_meta(self) -> None:
        ndjson = _session_meta_line("meta-session-id")
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson)
        assert result.session_id == "meta-session-id"

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
        assert raw["error_code"] == ""

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


class TestCodexResultParserHappyPath:
    def test_single_turn_success(self) -> None:
        """Composite happy-path: session_id, success, is_error=False, output, token_usage."""
        ndjson = "\n".join(
            [
                _thread_started_line("sess-42"),
                _item_completed_message_line("Task completed."),
                _turn_completed_line({"input_tokens": 200, "output_tokens": 80}),
            ]
        )
        parser = CodexResultParser()
        result = parser.parse_stdout(ndjson)
        assert result.success is True
        assert result.session_id == "sess-42"
        assert result.raw["is_error"] is False
        assert result.raw["subtype"] == "success"
        assert result.output == "Task completed."
        assert result.raw["token_usage"] == {"input_tokens": 200, "output_tokens": 80}
        assert result.raw["canonical_token_usage"] is not None


class TestCodexResultParserCumulativeTokens:
    def test_single_turn_canonical_matches_usage(self) -> None:
        """Scenario 1: single turn — canonical matches raw usage."""
        ndjson = _turn_completed_line({"input_tokens": 150, "output_tokens": 60})
        result = CodexResultParser().parse_stdout(ndjson)
        canonical = result.raw["canonical_token_usage"]
        assert canonical["input_tokens"] == 150
        assert canonical["output_tokens"] == 60

    def test_three_turns_last_wins_not_cumulative_sum(self) -> None:
        """Scenario 2: three turns (100/40, 220/90, 350/140) — last turn wins,
        NOT cumulative sum (670/270)."""
        ndjson = "\n".join(
            [
                _turn_completed_line({"input_tokens": 100, "output_tokens": 40}),
                _turn_completed_line({"input_tokens": 220, "output_tokens": 90}),
                _turn_completed_line({"input_tokens": 350, "output_tokens": 140}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        canonical = result.raw["canonical_token_usage"]
        assert canonical["input_tokens"] == 350
        assert canonical["output_tokens"] == 140

    def test_cache_read_mapped_and_cache_write_none(self) -> None:
        """Scenario 3: cached_input_tokens → cache_read_tokens; cache_write_tokens is None."""
        ndjson = _turn_completed_line(
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_input_tokens": 30,
            }
        )
        result = CodexResultParser().parse_stdout(ndjson)
        canonical = result.raw["canonical_token_usage"]
        assert canonical["cache_read_tokens"] == 30
        assert canonical["cache_write_tokens"] is None

    def test_no_turn_completed_yields_none_canonical(self) -> None:
        """Scenario 4: no turn.completed event — canonical_token_usage is None."""
        ndjson = _item_completed_message_line("some output")
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["canonical_token_usage"] is None
        assert result.raw["token_usage"] is None


class TestCodexResultParserErrorSession:
    def test_turn_failed_is_error_with_message(self) -> None:
        """turn.failed produces is_error=True with the error message extracted."""
        ndjson = _turn_failed_line("rate limit exceeded")
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["is_error"] is True
        assert result.success is False
        assert result.error == "rate limit exceeded"
        assert result.raw["subtype"] == "error_during_execution"

    def test_turn_failed_exit_code_forwarded(self) -> None:
        """exit_code parameter is forwarded on error path (not clamped to 1)."""
        ndjson = _turn_failed_line("process crashed")
        result = CodexResultParser().parse_stdout(ndjson, exit_code=137)
        assert result.exit_code == 137


class TestCodexStreamParserMarker:
    def test_standalone_marker_has_marker_true(self) -> None:
        """Standalone ORDER_UP marker in message → has_marker=True on turn.completed event."""
        marker = "%%ORDER_UP::abc123%%"
        parser = CodexStreamParser(completion_marker=marker)
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "message", "content": [{"type": "text", "text": marker}]},
            }
        )
        turn_line = json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}
        )
        parser.parse_line(msg_line)
        event = parser.parse_line(turn_line)
        assert event is not None
        assert event.has_marker is True

    def test_prose_containing_marker_has_marker_false(self) -> None:
        """Marker embedded in prose (not standalone line) → has_marker=False."""
        marker = "%%ORDER_UP::abc123%%"
        parser = CodexStreamParser(completion_marker=marker)
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "message",
                    "content": [
                        {"type": "text", "text": f"I found the text {marker} in the output"}
                    ],
                },
            }
        )
        turn_line = json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}
        )
        parser.parse_line(msg_line)
        event = parser.parse_line(turn_line)
        assert event is not None
        assert event.has_marker is False


class TestCodexResultParserSessionId:
    def test_thread_started_extracts_session_id(self) -> None:
        """thread.started thread_id is surfaced as session_id."""
        ndjson = "\n".join(
            [
                _thread_started_line("codex-thread-99"),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.session_id == "codex-thread-99"

    def test_session_meta_extracts_session_id(self) -> None:
        """session_meta payload.id is surfaced as session_id."""
        ndjson = "\n".join(
            [
                _session_meta_line("codex-meta-42"),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.session_id == "codex-meta-42"

    def test_missing_thread_started_yields_none_session_id(self) -> None:
        """No thread.started event → session_id is None (empty string coerced to None)."""
        ndjson = _turn_completed_line({"input_tokens": 1, "output_tokens": 1})
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.session_id is None


class TestCodexResultParserEdgeCases:
    def test_empty_stdout_is_error_no_crash(self) -> None:
        """Empty string → is_error=True, no exception raised."""
        result = CodexResultParser().parse_stdout("")
        assert result.raw["is_error"] is True
        assert result.success is False

    def test_non_json_lines_is_error_no_crash(self) -> None:
        """Non-JSON garbage → is_error=True (unparseable), no exception raised."""
        result = CodexResultParser().parse_stdout("not json\nalso not json\n{bad")
        assert result.raw["is_error"] is True
        assert result.raw["subtype"] == "unparseable"

    def test_only_messages_no_turn_events_is_error(self) -> None:
        """Messages without any turn.completed/turn.failed → is_error=True."""
        ndjson = "\n".join(
            [
                _item_completed_message_line("hello"),
                _item_completed_message_line("world"),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["is_error"] is True
        assert result.success is False


class TestCodexResultParserWriteArtifacts:
    def test_file_change_populates_file_changes(self) -> None:
        """Single file_change item → path appears in raw['file_changes']."""
        ndjson = "\n".join(
            [
                _item_completed_file_change_line("/src/main.py"),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["file_changes"] == ["/src/main.py"]

    def test_multiple_file_changes_accumulate(self) -> None:
        """Multiple file_change items → all paths accumulate in order."""
        ndjson = "\n".join(
            [
                _item_completed_file_change_line("/src/a.py"),
                _item_completed_file_change_line("/src/b.py"),
                _item_completed_file_change_line("/src/c.py"),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["file_changes"] == ["/src/a.py", "/src/b.py", "/src/c.py"]

    def test_file_change_delete_kind_path_stored(self) -> None:
        """file_change with kind=delete — path is still stored (only path is extracted)."""
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "file_change", "path": "/src/old.py", "kind": "delete"},
            }
        )
        ndjson = line + "\n" + _turn_completed_line({"input_tokens": 1, "output_tokens": 1})
        result = CodexResultParser().parse_stdout(ndjson)
        assert "/src/old.py" in result.raw["file_changes"]

    def test_item_started_file_change_excluded(self) -> None:
        """item.started with file_change type is NOT counted in file_changes."""
        ndjson = "\n".join(
            [
                _item_started_file_change_line("/src/started.py"),
                _item_completed_file_change_line("/src/completed.py"),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["file_changes"] == ["/src/completed.py"]
        assert "/src/started.py" not in result.raw["file_changes"]

    def test_file_change_empty_path_skipped(self) -> None:
        """file_change item with empty/missing path is silently skipped."""
        empty_path_line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "file_change", "path": ""},
            }
        )
        no_path_line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "file_change"},
            }
        )
        ndjson = "\n".join(
            [
                empty_path_line,
                no_path_line,
                _item_completed_file_change_line("/real.py"),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["file_changes"] == ["/real.py"]


class TestCodexResultParserToolUses:
    def test_function_call_populates_command_executions(self) -> None:
        """Single function_call item → stored in raw['command_executions']."""
        ndjson = "\n".join(
            [
                _item_completed_function_call_line("Bash", '{"command": "ls"}'),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert len(result.raw["command_executions"]) == 1
        assert result.raw["command_executions"][0]["name"] == "Bash"

    def test_multiple_function_calls_accumulate(self) -> None:
        """Multiple function_call items → all accumulate."""
        ndjson = "\n".join(
            [
                _item_completed_function_call_line("Bash", '{"command": "ls"}'),
                _item_completed_function_call_line("Read", '{"path": "/a.py"}'),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert len(result.raw["command_executions"]) == 2
        names = [e["name"] for e in result.raw["command_executions"]]
        assert names == ["Bash", "Read"]

    def test_function_call_nonzero_exit_preserved(self) -> None:
        """function_call item with exit_code field — stored as-is in the dict."""
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "function_call",
                    "name": "Bash",
                    "args": '{"command": "false"}',
                    "exit_code": 1,
                },
            }
        )
        ndjson = line + "\n" + _turn_completed_line({"input_tokens": 1, "output_tokens": 1})
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["command_executions"][0]["exit_code"] == 1

    def test_item_started_function_call_excluded(self) -> None:
        """item.started with function_call type is NOT counted in command_executions."""
        ndjson = "\n".join(
            [
                _item_started_function_call_line("Bash", '{"command": "echo start"}'),
                _item_completed_function_call_line("Bash", '{"command": "echo done"}'),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert len(result.raw["command_executions"]) == 1
        assert result.raw["command_executions"][0]["args"] == '{"command": "echo done"}'


class TestScanCodexNdjsonErrorCode:
    def test_scan_turn_failed_error_code_appended_to_message(self) -> None:
        ndjson = _turn_failed_code_line("context_length_exceeded", "Context window exceeded")
        acc = _scan_codex_ndjson(ndjson)
        assert "context_length_exceeded" in acc.error_message

    def test_scan_turn_failed_error_code_only_message_variant(self) -> None:
        ndjson = _turn_failed_line("context_length_exceeded")
        acc = _scan_codex_ndjson(ndjson)
        assert acc.error_message == "context_length_exceeded"


class TestCodexResultParserContextExhaustion:
    def test_parse_stdout_message_context_exhausted(self) -> None:
        ndjson = _turn_failed_line("context_length_exceeded")
        result = CodexResultParser().parse_stdout(ndjson)
        assert "context_length_exceeded" in result.error

    def test_parse_stdout_code_context_exhausted(self) -> None:
        ndjson = _turn_failed_code_line("context_length_exceeded", "Token limit reached.")
        result = CodexResultParser().parse_stdout(ndjson)
        assert "context_length_exceeded" in result.error

    def test_parse_stdout_rate_limit_not_context_exhausted(self) -> None:
        ndjson = _turn_failed_code_line("rate_limit_exceeded", "Rate limit exceeded.")
        result = CodexResultParser().parse_stdout(ndjson)
        assert "context_length_exceeded" not in result.error


def test_error_code_in_codex_raw_dict() -> None:
    line = json.dumps(
        {
            "type": "turn.failed",
            "error": {"message": "Token limit", "code": "context_length_exceeded"},
        }
    )
    result = CodexResultParser().parse_stdout(line)
    assert result.raw["error_code"] == "context_length_exceeded"


def test_error_code_empty_when_no_failure() -> None:
    line = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}
    )
    result = CodexResultParser().parse_stdout(line)
    assert result.raw["error_code"] == ""


# ---------------------------------------------------------------------------
# v0.136.0 schema helpers
# ---------------------------------------------------------------------------


def _item_completed_agent_message_line(text: str) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": text},
        }
    )


def _item_completed_command_execution_line(command: str) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": command},
        }
    )


def _item_completed_file_change_nested_line(paths: list[str]) -> str:
    return json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "file_change",
                "changes": [{"path": p, "kind": "add"} for p in paths],
            },
        }
    )


class TestCodexResultParserV0136Schema:
    def test_agent_message_populates_agent_messages(self) -> None:
        ndjson = "\n".join(
            [
                _item_completed_agent_message_line("hello from v0.136"),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["agent_messages"] == ["hello from v0.136"]
        assert result.output == "hello from v0.136"

    def test_command_execution_populates_command_executions(self) -> None:
        ndjson = "\n".join(
            [
                _item_completed_command_execution_line("ls -la"),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert len(result.raw["command_executions"]) == 1
        assert result.raw["command_executions"][0]["command"] == "ls -la"

    def test_file_change_nested_populates_file_changes(self) -> None:
        ndjson = "\n".join(
            [
                _item_completed_file_change_nested_line(["/src/a.py", "/src/b.py"]),
                _turn_completed_line({"input_tokens": 1, "output_tokens": 1}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.raw["file_changes"] == ["/src/a.py", "/src/b.py"]

    def test_v0136_composite_session_output_nonempty(self) -> None:
        ndjson = "\n".join(
            [
                _thread_started_line("v0136-session"),
                _item_completed_agent_message_line("Task done."),
                _item_completed_command_execution_line("git status"),
                _item_completed_file_change_nested_line(["/src/main.py"]),
                _turn_completed_line({"input_tokens": 200, "output_tokens": 80}),
            ]
        )
        result = CodexResultParser().parse_stdout(ndjson)
        assert result.success is True
        assert result.output
        assert len(result.raw["agent_messages"]) > 0
        assert len(result.raw["command_executions"]) > 0
        assert len(result.raw["file_changes"]) > 0
