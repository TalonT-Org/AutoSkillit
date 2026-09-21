"""Parent-assistant transcript filtering and logical-turn merging.

This stdlib-only module is shared by package consumers and standalone hook
projections.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Iterator
from typing import NamedTuple

TOOL_USE_CAP = 8


class AssistantTurn(NamedTuple):
    request_id: str
    timestamp: str
    tool_names: tuple[str, ...]


def _resolve_turn_id(rec: dict[str, object]) -> str:
    """Return a nonempty turn-grouping key for a transcript record.

    Resolution order: a non-empty ``requestId`` wins, then a non-empty
    ``message.id``. An empty string is returned when neither key resolves,
    signalling that the record belongs to its own standalone turn.
    """
    request_id = rec.get("requestId", "")
    if isinstance(request_id, str) and request_id:
        return request_id
    message = rec.get("message")
    if isinstance(message, dict):
        message_id = message.get("id", "")
        if isinstance(message_id, str) and message_id:
            return message_id
    return ""


def is_parent_assistant_record(rec: object) -> bool:
    """Return True iff ``rec`` is a transcript record for the parent assistant.

    A record is the parent assistant iff:

    - it is a mapping,
    - ``type`` is ``"assistant"`` (excluding ``user``/``system``/tool records),
    - it is not a Task subagent record (``subagent_type`` is unset), and
    - its ``message.model`` is not the ``"<synthetic>"`` placeholder that
      Claude emits for non-conversational assistant messages.

    Non-mapping inputs (including raw JSON scalars) return False rather than
    raising, so callers can pass the output of ``json.loads()`` directly.
    """
    if not isinstance(rec, dict):
        return False
    if rec.get("type") != "assistant":
        return False
    if rec.get("subagent_type"):
        return False
    message = rec.get("message")
    return not (isinstance(message, dict) and message.get("model") == "<synthetic>")


def iter_merged_assistant_turns(text: str, *, cap: int = TOOL_USE_CAP) -> Iterator[AssistantTurn]:
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
        # ``message.get("content")`` may be present-but-null in malformed
        # transcripts; ``or []`` coerces both missing and null to an empty
        # iterable so a single bad record cannot abort the iterator.
        content = (message.get("content") or []) if isinstance(message, dict) else []
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
