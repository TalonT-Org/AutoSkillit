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


def _resolve_codex_turn_id(rec: dict[str, object], active_turn_id: str) -> str:
    payload = rec.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    for value in (
        _resolve_turn_id(rec),
        rec.get("turn_id"),
        payload.get("turn_id"),
        active_turn_id,
        payload.get("id"),
        payload.get("call_id"),
    ):
        if isinstance(value, str) and value:
            return value
    return ""


def _codex_assistant_payload(rec: object) -> dict[str, object] | None:
    if not isinstance(rec, dict) or rec.get("type") != "response_item":
        return None
    payload = rec.get("payload")
    if not isinstance(payload, dict) or payload.get("agent_id") or payload.get("agentId"):
        return None
    payload_type = payload.get("type")
    if payload_type == "message" and payload.get("role") == "assistant":
        return payload
    if payload_type in {"function_call", "custom_tool_call"}:
        return payload
    return None


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


def _assistant_content(
    record: dict[str, object], backend: str, active_codex_turn_id: str, is_claude_assistant: bool
) -> tuple[str | None, str | list[dict[str, object]] | None, str] | None:
    if backend == "codex":
        context = record.get("payload")
        if (
            record.get("type") == "turn_context"
            and isinstance(context, dict)
            and isinstance(context.get("turn_id"), str)
        ):
            active_codex_turn_id = context["turn_id"]
        message = _codex_assistant_payload(record)
        if message is None:
            return None, [], active_codex_turn_id
        turn_id = _resolve_codex_turn_id(record, active_codex_turn_id)
        if message.get("type") in {"function_call", "custom_tool_call"}:
            return (
                turn_id,
                [{"type": "tool_use", "name": message.get("name")}],
                active_codex_turn_id,
            )
        codex_content = message.get("content")
        return (
            turn_id,
            codex_content if isinstance(codex_content, (list, str)) else [],
            active_codex_turn_id,
        )
    if not is_claude_assistant:
        return None
    claude_message = record.get("message")
    if isinstance(claude_message, dict):
        claude_content = claude_message.get("content")
        content: str | list[dict[str, object]] | None = (
            claude_content if isinstance(claude_content, (list, str)) else []
        )
    else:
        content = []
    return _resolve_turn_id(record), content, active_codex_turn_id


def _iter_transcript_records(text: str) -> Iterator[dict[str, object]]:
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            yield record


def iter_merged_assistant_turns(
    text: str, *, cap: int = TOOL_USE_CAP, backend: str = "claude"
) -> Iterator[AssistantTurn]:
    """Yield merged parent-assistant turns in their first-seen transcript order.

    A nonempty ``requestId`` is the preferred turn-grouping key, with
    ``message.id`` as the alternative. Records without either key are distinct
    turns. Tool fragments are accumulated before the cap is applied. ``backend``
    selects Claude transcript records or native Codex rollout records.
    """
    if backend not in {"claude", "codex"}:
        raise ValueError(f"Unsupported transcript backend: {backend}")
    pending: OrderedDict[str, tuple[str, list[str]]] = OrderedDict()
    insertion_order: list[tuple[str, str]] = []
    no_id_turns: dict[str, AssistantTurn] = {}
    no_id_counter = 0
    active_codex_turn_id = ""

    for record in _iter_transcript_records(text):
        resolved = _assistant_content(
            record, backend, active_codex_turn_id, is_parent_assistant_record(record)
        )
        if resolved is None:
            continue
        turn_id, raw_content, active_codex_turn_id = resolved
        if turn_id is None:
            continue

        content = raw_content if isinstance(raw_content, list) else []

        timestamp = record.get("timestamp", "")
        if not isinstance(timestamp, str):
            timestamp = ""
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
