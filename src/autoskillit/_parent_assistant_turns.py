"""Parent-assistant transcript filtering and logical-turn merging.

This stdlib-only module is shared by package consumers and standalone hook
projections.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Iterator
from typing import NamedTuple

_TOOL_USE_CAP = 8


class AssistantTurn(NamedTuple):
    request_id: str
    timestamp: str
    tool_names: tuple[str, ...]


def _resolve_turn_id(rec: dict[str, object]) -> str:
    """Return a nonempty requestId or message.id turn-grouping key, in that order."""
    request_id = rec.get("requestId", "")
    if isinstance(request_id, str) and request_id:
        return request_id
    message = rec.get("message")
    if isinstance(message, dict):
        message_id = message.get("id", "")
        if isinstance(message_id, str) and message_id:
            return message_id
    return ""


def is_parent_assistant_record(rec: dict[str, object]) -> bool:
    """Whether a transcript record belongs to the parent assistant."""
    if rec.get("type") != "assistant":
        return False
    if rec.get("subagent_type"):
        return False
    message = rec.get("message")
    return not (isinstance(message, dict) and message.get("model") == "<synthetic>")


def iter_merged_assistant_turns(text: str, *, cap: int = _TOOL_USE_CAP) -> Iterator[AssistantTurn]:
    """Yield merged parent-assistant turns in their first-seen transcript order.

    A nonempty ``requestId`` is the preferred turn-grouping key, with
    ``message.id`` as the alternative. Records without either key are distinct
    turns. Tool fragments are accumulated before the cap is applied.
    """
    pending: OrderedDict[str, tuple[str, list[str]]] = OrderedDict()
    insertion_order: list[tuple[str, str]] = []
    no_id_turns: dict[str, AssistantTurn] = {}
    no_id_counter = 0

    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or not is_parent_assistant_record(record):
            continue

        turn_id = _resolve_turn_id(record)
        timestamp = record.get("timestamp", "")
        message = record.get("message")
        content = message.get("content", []) if isinstance(message, dict) else []
        tools = [
            str(block["name"])
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "tool_use"
            and isinstance(block.get("name"), str)
            and block["name"]
        ]

        if turn_id:
            if turn_id in pending:
                existing_timestamp, existing_tools = pending[turn_id]
                pending[turn_id] = (existing_timestamp or timestamp, existing_tools + tools)
            else:
                pending[turn_id] = (timestamp, tools)
                insertion_order.append(("id", turn_id))
        else:
            key = str(no_id_counter)
            no_id_counter += 1
            no_id_turns[key] = AssistantTurn(f"turn-{key}", timestamp, tuple(tools[:cap]))
            insertion_order.append(("no-id", key))

    for kind, key in insertion_order:
        if kind == "id":
            timestamp, tools = pending[key]
            yield AssistantTurn(key, timestamp, tuple(tools[:cap]))
        else:
            yield no_id_turns[key]
