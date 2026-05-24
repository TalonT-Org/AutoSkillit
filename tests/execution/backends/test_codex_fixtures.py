"""Codex fixture integrity tests."""

from __future__ import annotations

import json

import pytest

from autoskillit.execution.backends._codex_parse import _scan_codex_ndjson
from autoskillit.execution.process import _marker_is_standalone
from tests.fixtures.codex import (
    CODEX_SCHEMA_VERSION,
    HAPPY_PATH_SINGLE_TURN,
    MULTI_TURN_WITH_COMPACTION,
    SESSION_WITH_MCP_TOOL_CALL,
    SESSION_WITH_REASONING,
    TURN_FAILED_ERROR,
    fixture_path,
)
from tests.fixtures.codex import (
    __all__ as CODEX_ALL,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

ALL_FIXTURE_NAMES = [
    HAPPY_PATH_SINGLE_TURN,
    MULTI_TURN_WITH_COMPACTION,
    TURN_FAILED_ERROR,
    SESSION_WITH_REASONING,
    SESSION_WITH_MCP_TOOL_CALL,
]


def _load_events(name: str) -> list[dict]:
    """Read a fixture and return parsed JSON objects."""
    text = fixture_path(name).read_text()
    return [json.loads(line) for line in text.strip().splitlines() if line.strip()]


class TestCodexFixturePackage:
    def test_schema_version_is_one(self) -> None:
        assert CODEX_SCHEMA_VERSION == 1

    def test_all_filename_constants_end_in_ndjson(self) -> None:
        for name in ALL_FIXTURE_NAMES:
            assert name.endswith(".ndjson")

    def test_fixture_path_returns_existing_file(self) -> None:
        for name in ALL_FIXTURE_NAMES:
            assert fixture_path(name).is_file()

    def test_all_exports_count(self) -> None:
        assert len(CODEX_ALL) == 7


class TestCodexFixtureValidity:
    def test_every_line_is_valid_json(self) -> None:
        name = HAPPY_PATH_SINGLE_TURN
        text = fixture_path(name).read_text()
        for line in text.strip().splitlines():
            if line.strip():
                json.loads(line)

    def test_first_line_is_thread_started(self) -> None:
        name = HAPPY_PATH_SINGLE_TURN
        events = _load_events(name)
        assert events[0]["type"] == "thread.started"
        assert isinstance(events[0]["thread_id"], str)
        assert events[0]["thread_id"]

    def test_all_lines_are_dicts(self) -> None:
        name = HAPPY_PATH_SINGLE_TURN
        events = _load_events(name)
        for event in events:
            assert isinstance(event, dict)


class TestHappyPathFixture:
    def test_contains_order_up_marker_standalone(self) -> None:
        events = _load_events(HAPPY_PATH_SINGLE_TURN)
        for event in events:
            if (
                event.get("type") == "item.completed"
                and event.get("item", {}).get("type") == "message"
            ):
                for block in event.get("item", {}).get("content", []):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if "%%ORDER_UP%%" in text:
                            assert _marker_is_standalone(text, "%%ORDER_UP%%")
                            return
        pytest.fail("%%ORDER_UP%% marker not found in any item.completed message event")

    def test_turn_completed_has_nonzero_usage(self) -> None:
        events = _load_events(HAPPY_PATH_SINGLE_TURN)
        for event in events:
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
                assert usage.get("input_tokens", 0) > 0
                assert usage.get("output_tokens", 0) > 0
                return
        pytest.fail("No turn.completed event found in fixture")

    def test_reasoning_output_tokens_zero(self) -> None:
        events = _load_events(HAPPY_PATH_SINGLE_TURN)
        for event in events:
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
                assert usage.get("reasoning_output_tokens", 0) == 0
                return
        pytest.fail("No turn.completed event found in fixture")

    def test_has_three_item_types(self) -> None:
        events = _load_events(HAPPY_PATH_SINGLE_TURN)
        item_types = set()
        for event in events:
            if event.get("type") == "item.completed":
                item_types.add(event.get("item", {}).get("type"))
        assert "function_call" in item_types
        assert "file_change" in item_types
        assert "message" in item_types


class TestMultiTurnFixture:
    def test_turn2_input_tokens_greater_than_turn1(self) -> None:
        events = _load_events(MULTI_TURN_WITH_COMPACTION)
        turn_completed_tokens = [
            e.get("usage", {}).get("input_tokens", 0)
            for e in events
            if e.get("type") == "turn.completed"
        ]
        assert len(turn_completed_tokens) == 2
        assert turn_completed_tokens[1] > turn_completed_tokens[0]

    def test_has_two_turn_completed_events(self) -> None:
        events = _load_events(MULTI_TURN_WITH_COMPACTION)
        count = sum(1 for e in events if e.get("type") == "turn.completed")
        assert count == 2

    def test_todo_list_item_present(self) -> None:
        events = _load_events(MULTI_TURN_WITH_COMPACTION)
        has_todo = any(
            e.get("type") == "item.completed" and e.get("item", {}).get("type") == "todo_list"
            for e in events
        )
        assert has_todo


class TestTurnFailedFixture:
    def test_has_nonempty_error_message(self) -> None:
        events = _load_events(TURN_FAILED_ERROR)
        for event in events:
            if event.get("type") == "turn.failed":
                assert event.get("error", {}).get("message")
                return
        pytest.fail("No turn.failed event found in fixture")

    def test_has_partial_item_started(self) -> None:
        events = _load_events(TURN_FAILED_ERROR)
        has_item_started = any(e.get("type") == "item.started" for e in events)
        assert has_item_started

    def test_no_turn_completed(self) -> None:
        events = _load_events(TURN_FAILED_ERROR)
        has_completed = any(e.get("type") == "turn.completed" for e in events)
        assert not has_completed


class TestReasoningFixture:
    def test_reasoning_output_tokens_positive(self) -> None:
        events = _load_events(SESSION_WITH_REASONING)
        for event in events:
            if event.get("type") == "turn.completed":
                assert event.get("usage", {}).get("reasoning_output_tokens", 0) > 0
                return
        pytest.fail("No turn.completed event found in fixture")

    def test_has_reasoning_item(self) -> None:
        events = _load_events(SESSION_WITH_REASONING)
        has_reasoning = any(
            e.get("type") == "item.completed"
            and e.get("item", {}).get("type") == "reasoning"
            and e.get("item", {}).get("text")
            for e in events
        )
        assert has_reasoning

    def test_contains_order_up_marker(self) -> None:
        events = _load_events(SESSION_WITH_REASONING)
        for event in events:
            if (
                event.get("type") == "item.completed"
                and event.get("item", {}).get("type") == "message"
            ):
                for block in event.get("item", {}).get("content", []):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if "%%ORDER_UP%%" in text:
                            assert _marker_is_standalone(text, "%%ORDER_UP%%")
                            return
        pytest.fail("%%ORDER_UP%% marker not found in any item.completed message event")


class TestMcpToolCallFixture:
    def test_has_all_three_status_transitions(self) -> None:
        events = _load_events(SESSION_WITH_MCP_TOOL_CALL)
        mcp_events = [
            e.get("type") for e in events if e.get("item", {}).get("type") == "mcp_tool_call"
        ]
        assert "item.started" in mcp_events
        assert "item.updated" in mcp_events
        assert "item.completed" in mcp_events

    def test_mcp_result_content_populated(self) -> None:
        events = _load_events(SESSION_WITH_MCP_TOOL_CALL)
        for event in events:
            if (
                event.get("type") == "item.completed"
                and event.get("item", {}).get("type") == "mcp_tool_call"
            ):
                result = event.get("item", {}).get("result", {})
                assert result.get("content")
                return
        pytest.fail("No item.completed mcp_tool_call event found in fixture")


class TestCodexFixturesParseWithBackend:
    def test_happy_path_parses_successfully(self) -> None:
        text = fixture_path(HAPPY_PATH_SINGLE_TURN).read_text()
        acc = _scan_codex_ndjson(text)
        assert acc.success is True
        assert acc.session_id

    def test_failed_parses_as_failure(self) -> None:
        text = fixture_path(TURN_FAILED_ERROR).read_text()
        acc = _scan_codex_ndjson(text)
        assert acc.success is False
        assert acc.error_message
