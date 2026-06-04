from __future__ import annotations

import json

import pytest
import structlog.testing

from autoskillit.core import (
    BackendEventKind,
    CanonicalTokenUsage,
    CodexEventData,
    CodexItemType,
    StreamParser,
)
from autoskillit.execution.backends._codex_parse import (
    CodexStreamParser,
    _scan_codex_ndjson,
)
from tests.fixtures.codex import (
    HAPPY_PATH_SINGLE_TURN,
    HAPPY_PATH_V0136,
    MARKER_DETECTION_V0136,
    MULTI_TURN_WITH_COMPACTION,
    TURN_FAILED_ERROR,
    fixture_path,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexStreamParserHappyPath:
    def test_thread_started_yields_session_meta(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "thread.started", "thread_id": "t1"})
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.SESSION_META
        assert event.is_terminal is False
        assert event.has_marker is False
        assert event.session_id == "t1"

    def test_turn_completed_yields_terminal_completion_with_usage(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 150,
                    "output_tokens": 75,
                    "cached_input_tokens": 30,
                },
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.COMPLETION
        assert event.is_terminal is True
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.usage is not None
        assert isinstance(event.backend_data.usage, dict)
        usage = CanonicalTokenUsage.from_codex_dict(event.backend_data.usage)
        assert usage.input_tokens == 150
        assert usage.output_tokens == 75
        assert usage.cache_read_tokens == 30
        assert usage.cache_write_tokens is None
        assert usage.provider == "codex"

    def test_turn_failed_yields_terminal_completion(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "turn.failed",
                "error": {"message": "Rate limit exceeded", "code": "rate_limit_exceeded"},
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.COMPLETION
        assert event.is_terminal is True
        assert event.has_marker is False
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.record_type == "turn.failed"

    def test_thread_started_missing_thread_id_yields_none_session_id(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "thread.started"})
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.SESSION_META
        assert event.session_id is None

    def test_error_yields_terminal_error(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "error", "message": "crash"})
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.ERROR
        assert event.is_terminal is True
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.record_type == "error"


class TestCodexStreamParserItemCompleted:
    def test_standalone_marker_detected_on_completion(self) -> None:
        parser = CodexStreamParser(completion_marker="%%ORDER_UP%%")
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "message",
                    "content": [{"type": "text", "text": "Done.\n\n%%ORDER_UP%%"}],
                },
            }
        )
        parser.parse_line(msg_line)
        turn_completed_line = json.dumps({"type": "turn.completed", "usage": {}})
        event = parser.parse_line(turn_completed_line)
        assert event is not None
        assert event.has_marker is True

    def test_embedded_marker_not_detected(self) -> None:
        parser = CodexStreamParser(completion_marker="%%ORDER_UP%%")
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "message",
                    "content": [{"type": "text", "text": "The %%ORDER_UP%% token was emitted."}],
                },
            }
        )
        parser.parse_line(msg_line)
        turn_completed_line = json.dumps({"type": "turn.completed", "usage": {}})
        event = parser.parse_line(turn_completed_line)
        assert event is not None
        assert event.has_marker is False

    def test_no_marker_in_message_yields_false(self) -> None:
        parser = CodexStreamParser(completion_marker="%%ORDER_UP%%")
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "message",
                    "content": [{"type": "text", "text": "All done, no marker here."}],
                },
            }
        )
        parser.parse_line(msg_line)
        turn_completed_line = json.dumps({"type": "turn.completed", "usage": {}})
        event = parser.parse_line(turn_completed_line)
        assert event is not None
        assert event.has_marker is False

    def test_no_marker_when_completion_marker_empty(self) -> None:
        parser = CodexStreamParser()
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "message",
                    "content": [{"type": "text", "text": "%%ORDER_UP%%"}],
                },
            }
        )
        parser.parse_line(msg_line)
        turn_completed_line = json.dumps({"type": "turn.completed", "usage": {}})
        event = parser.parse_line(turn_completed_line)
        assert event is not None
        assert event.has_marker is False

    def test_last_turn_completed_carries_accumulated_marker_state(self) -> None:
        # _saw_marker is a permanent latch: once set True by any item.completed
        # message in the session, it stays True for all subsequent turn.completed
        # events. The second turn here feeds a message without the marker, but
        # event2.has_marker is still True because the latch never resets.
        parser = CodexStreamParser(completion_marker="%%ORDER_UP%%")
        msg_with_marker = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "message",
                    "content": [{"type": "text", "text": "Done.\n\n%%ORDER_UP%%"}],
                },
            }
        )
        msg_without_marker = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "message",
                    "content": [{"type": "text", "text": "More work done."}],
                },
            }
        )
        turn_completed_line = json.dumps({"type": "turn.completed", "usage": {}})

        parser.parse_line(msg_with_marker)
        event1 = parser.parse_line(turn_completed_line)
        assert event1 is not None
        assert event1.kind == BackendEventKind.COMPLETION
        assert event1.has_marker is True

        parser.parse_line(msg_without_marker)
        event2 = parser.parse_line(turn_completed_line)
        assert event2 is not None
        assert event2.kind == BackendEventKind.COMPLETION
        assert event2.has_marker is True

    def test_item_completed_message_yields_tool_output(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "message", "content": [{"type": "text", "text": "hello"}]},
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT
        assert event.is_terminal is False
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.record_type == "item.completed"
        assert event.backend_data.item_type == "message"

    def test_item_completed_function_call_yields_tool_output(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {"type": "item.completed", "item": {"type": "function_call", "name": "Bash"}}
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT
        assert event.is_terminal is False
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.item_type == "function_call"

    def test_file_change_with_path_populated(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "file_change",
                    "id": "fch_1",
                    "path": "/src/foo.py",
                    "changes": [{"type": "add", "line": 10}],
                },
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT
        assert event.is_terminal is False
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.item_type == "file_change"
        assert event.backend_data.raw["item"]["changes"] == [{"type": "add", "line": 10}]

    def test_file_change_with_status_failed(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "file_change",
                    "id": "fch_2",
                    "path": "/src/bar.py",
                    "status": "failed",
                },
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.raw["item"]["status"] == "failed"

    def test_file_change_with_empty_changes(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "file_change",
                    "id": "fch_3",
                    "path": "/src/baz.py",
                    "changes": [],
                },
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.raw["item"]["changes"] == []


class TestCodexStreamParserDegradation:
    def test_unknown_top_level_event_type_yields_ignored(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "something.unexpected", "data": 42})
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.IGNORED
        assert event.is_terminal is False
        assert event.has_marker is False

    def test_item_completed_unknown_item_type_yields_ignored(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {"type": "item.completed", "item": {"type": "reasoning", "text": "thinking..."}}
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.IGNORED

    def test_malformed_json_returns_none(self) -> None:
        parser = CodexStreamParser()
        event = parser.parse_line('{"type": "turn.comple')
        assert event is None

    def test_empty_string_returns_none(self) -> None:
        parser = CodexStreamParser()
        assert parser.parse_line("") is None
        assert parser.parse_line("   ") is None


class TestCodexStreamParserFixtures:
    def test_happy_path_first_event_session_meta_last_completion(self) -> None:
        text = fixture_path(HAPPY_PATH_SINGLE_TURN).read_text()
        parser = CodexStreamParser(completion_marker="%%ORDER_UP%%")
        events = [
            ev
            for line in text.strip().splitlines()
            if line.strip()
            for ev in [parser.parse_line(line)]
            if ev is not None
        ]
        assert events[0].kind == BackendEventKind.SESSION_META
        terminal_events = [e for e in events if e.is_terminal]
        assert len(terminal_events) == 1
        assert terminal_events[0].kind == BackendEventKind.COMPLETION
        assert terminal_events[0].has_marker is True

    def test_multi_turn_last_turn_completed_has_final_usage(self) -> None:
        text = fixture_path(MULTI_TURN_WITH_COMPACTION).read_text()
        parser = CodexStreamParser()
        events = [
            ev
            for line in text.strip().splitlines()
            if line.strip()
            for ev in [parser.parse_line(line)]
            if ev is not None
        ]
        completions = [e for e in events if e.kind == BackendEventKind.COMPLETION]
        assert len(completions) == 2
        assert isinstance(completions[0].backend_data, CodexEventData)
        assert isinstance(completions[1].backend_data, CodexEventData)
        first_usage = completions[0].backend_data.usage
        second_usage = completions[1].backend_data.usage
        assert first_usage is not None
        assert second_usage is not None
        # Compaction causes cumulative input growth: turn 2 includes the compacted
        # summary of turn 1, so input_tokens is monotonically increasing across turns.
        assert second_usage["input_tokens"] > first_usage["input_tokens"]

    def test_turn_failed_fixture_yields_terminal_event(self) -> None:
        text = fixture_path(TURN_FAILED_ERROR).read_text()
        parser = CodexStreamParser()
        events = [
            ev
            for line in text.strip().splitlines()
            if line.strip()
            for ev in [parser.parse_line(line)]
            if ev is not None
        ]
        terminal_events = [e for e in events if e.is_terminal]
        assert len(terminal_events) == 1
        terminal = terminal_events[0]
        assert terminal.kind == BackendEventKind.COMPLETION
        assert terminal.is_terminal is True
        assert isinstance(terminal.backend_data, CodexEventData)
        assert terminal.backend_data.record_type == "turn.failed"


class TestCodexStreamParserConformance:
    def test_isinstance_stream_parser_protocol(self) -> None:
        assert isinstance(CodexStreamParser(), StreamParser)


class TestCodexStreamParserV0136Schema:
    def test_agent_message_yields_tool_output(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "hello from v0.136"},
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.item_type == "agent_message"

    def test_command_execution_yields_tool_output(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "command": "ls -la"},
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.item_type == "command_execution"

    def test_agent_message_marker_detected(self) -> None:
        marker = "%%ORDER_UP%%"
        parser = CodexStreamParser(completion_marker=marker)
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": f"Done.\n\n{marker}"},
            }
        )
        parser.parse_line(msg_line)
        turn_line = json.dumps({"type": "turn.completed", "usage": {}})
        event = parser.parse_line(turn_line)
        assert event is not None
        assert event.has_marker is True

    def test_agent_message_empty_marker_no_false_positive(self) -> None:
        parser = CodexStreamParser(completion_marker="")
        msg_line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "some text\n\n"},
            }
        )
        parser.parse_line(msg_line)
        turn_line = json.dumps({"type": "turn.completed", "usage": {}})
        event = parser.parse_line(turn_line)
        assert event is not None
        assert event.has_marker is False

    def test_mcp_tool_call_yields_tool_output(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "mcp_tool_call", "tool_name": "test_tool"},
            }
        )
        event = parser.parse_line(line)
        assert event is not None
        assert event.kind == BackendEventKind.TOOL_OUTPUT
        assert isinstance(event.backend_data, CodexEventData)
        assert event.backend_data.item_type == "mcp_tool_call"


class TestCodexStreamParserV0136Fixtures:
    def test_v0136_happy_path_marker_detected(self) -> None:
        text = fixture_path(HAPPY_PATH_V0136).read_text()
        parser = CodexStreamParser(completion_marker="%%ORDER_UP%%")
        events = [
            ev
            for line in text.strip().splitlines()
            if line.strip()
            for ev in [parser.parse_line(line)]
            if ev is not None
        ]
        assert events[0].kind == BackendEventKind.SESSION_META
        terminal_events = [e for e in events if e.is_terminal]
        assert len(terminal_events) == 1
        assert terminal_events[0].has_marker is True

    def test_v0136_marker_detection_fixture(self) -> None:
        text = fixture_path(MARKER_DETECTION_V0136).read_text()
        parser = CodexStreamParser(completion_marker="%%ORDER_UP%%")
        events = [
            ev
            for line in text.strip().splitlines()
            if line.strip()
            for ev in [parser.parse_line(line)]
            if ev is not None
        ]
        terminal_events = [e for e in events if e.is_terminal]
        assert len(terminal_events) == 1
        assert terminal_events[0].has_marker is True


class TestCodexParserParity:
    _INFORMATIONAL = {CodexItemType.REASONING, CodexItemType.TODO_LIST}
    _SKIP = {CodexItemType.UNKNOWN} | _INFORMATIONAL

    def test_both_parsers_handle_same_item_types(self) -> None:
        for member in CodexItemType:
            if member in self._SKIP:
                continue
            item: dict = {"type": member.value}
            if member == CodexItemType.AGENT_MESSAGE:
                item["text"] = "test"
            elif member == CodexItemType.MESSAGE:
                item["content"] = [{"type": "text", "text": "test"}]
            elif member == CodexItemType.FILE_CHANGE:
                item["path"] = "/test.py"
            ndjson_line = json.dumps({"type": "item.completed", "item": item})
            acc = _scan_codex_ndjson(ndjson_line)
            batch_has_data = (
                acc.agent_messages
                or acc.command_executions
                or acc.mcp_tool_calls
                or acc.file_changes
            )
            assert batch_has_data, f"batch parser silently dropped {member.value}"
            parser = CodexStreamParser()
            event = parser.parse_line(ndjson_line)
            assert event is not None, f"stream parser returned None for {member.value}"
            assert event.kind != BackendEventKind.IGNORED, (
                f"stream parser returned IGNORED for {member.value}"
            )

    def test_informational_items_ignored_by_both(self) -> None:
        for member in self._INFORMATIONAL:
            item: dict = {"type": member.value, "text": "info"}
            ndjson_line = json.dumps({"type": "item.completed", "item": item})
            acc = _scan_codex_ndjson(ndjson_line)
            assert not acc.agent_messages
            assert not acc.command_executions
            parser = CodexStreamParser()
            event = parser.parse_line(ndjson_line)
            assert event is not None
            assert event.kind == BackendEventKind.IGNORED


class TestCodexParserUnrecognizedTypeWarning:
    def test_unknown_event_type_logs_warning(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps({"type": "brand_new_event_type", "data": 1})
        with structlog.testing.capture_logs() as cap:
            parser.parse_line(line)
        assert any(
            log["event"] == "codex_ndjson_unknown_event_type" and log["log_level"] == "warning"
            for log in cap
        )

    def test_unknown_item_type_logs_warning(self) -> None:
        parser = CodexStreamParser()
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "brand_new_item_type", "data": 1},
            }
        )
        with structlog.testing.capture_logs() as cap:
            parser.parse_line(line)
        assert any(
            log["event"] == "codex_ndjson_unknown_item_type" and log["log_level"] == "warning"
            for log in cap
        )
