"""Keep the live and post-hoc Codex item dispatchers aligned."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import validate

from autoskillit.core import BackendEventKind, CodexEventData
from autoskillit.core.types.foundation._type_enums import CodexItemType
from autoskillit.execution.backends._codex_parse import (
    CodexStreamParser,
    _accumulate_codex_completed_item,
    _CodexParseAccumulator,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "codex_ndjson"
_ITEM_EXAMPLES: dict[str, dict[str, object]] = {
    "agent_message": {"type": "agent_message", "text": "message"},
    "command_execution": {"type": "command_execution"},
    "file_change": {"type": "file_change"},
    "mcp_tool_call": {"type": "mcp_tool_call"},
    "collab_tool_call": {"type": "collab_tool_call"},
    "web_search": {"type": "web_search"},
    "message": {"type": "message", "content": [{"type": "text", "text": "message"}]},
    "function_call": {"type": "function_call"},
    "reasoning": {"type": "reasoning"},
    "todo_list": {"type": "todo_list"},
    "error": {"type": "error", "message": "item error"},
}


@pytest.mark.parametrize(
    "member",
    [member for member in CodexItemType if member != CodexItemType.UNKNOWN],
    ids=lambda member: member.value,
)
def test_completed_item_dispatchers_recognize_each_sealed_type(member: CodexItemType) -> None:
    item = _ITEM_EXAMPLES[member.value]
    schema = json.loads((_FIXTURES_DIR / f"item_{member.value}.json").read_text(encoding="utf-8"))
    validate(item, schema)
    event = {"type": "item.completed", "item": item}

    accumulator = _CodexParseAccumulator()
    _accumulate_codex_completed_item(accumulator, event)
    assert accumulator.ndjson_unknown_item_count == 0

    live_parser = CodexStreamParser()
    live_event = live_parser.parse_line(json.dumps(event))
    assert live_parser.ndjson_unknown_item_count == 0
    assert live_event is not None

    if member.value == "error":
        assert member is CodexItemType.ERROR
        assert accumulator.item_error_messages == ["item error"]
        assert live_event.kind is BackendEventKind.IGNORED
        assert isinstance(live_event.backend_data, CodexEventData)
        assert live_event.backend_data.raw["item"]["message"] == "item error"


def test_error_item_messages_ignore_empty_and_non_string_values() -> None:
    accumulator = _CodexParseAccumulator()
    for message in ("", None, 7):
        _accumulate_codex_completed_item(
            accumulator,
            {"type": "item.completed", "item": {"type": "error", "message": message}},
        )

    assert accumulator.item_error_messages == []
