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


def _codex_record_content(
    record: dict[str, object], active_turn_id: str
) -> tuple[str | None, str | list[object], str]:
    """Return ``(turn_id, content, next_active_turn_id)`` for a codex record.

    When the record carries no assistant payload, the returned ``turn_id`` is
    ``None`` so the caller can propagate the (possibly updated) active turn id
    without yielding a fresh turn. ``active_turn_id`` advances whenever a
    ``turn_context`` record precedes the assistant payload.
    """
    context = record.get("payload")
    next_active = active_turn_id
    if (
        record.get("type") == "turn_context"
        and isinstance(context, dict)
        and isinstance(context.get("turn_id"), str)
    ):
        next_active = context["turn_id"]
    message = _codex_assistant_payload(record)
    if message is None:
        return None, [], next_active
    turn_id = _resolve_codex_turn_id(record, next_active)
    if message.get("type") in {"function_call", "custom_tool_call"}:
        return turn_id, [{"type": "tool_use", "name": message.get("name")}], next_active
    codex_content = message.get("content")
    return turn_id, codex_content if isinstance(codex_content, (list, str)) else [], next_active


def _claude_record_content(
    record: dict[str, object],
) -> tuple[str | None, str | list[object]]:
    """Return ``(turn_id, content)`` for a claude record. Always returns a tuple;
    callers skip non-parent-assistant records before calling this helper."""
    claude_message = record.get("message")
    content: str | list[object] = []
    if isinstance(claude_message, dict):
        claude_content = claude_message.get("content")
        content = claude_content if isinstance(claude_content, (list, str)) else []
    return _resolve_turn_id(record), content


def _claude_record_content_with_predicate(
    record: dict[str, object], is_parent_assistant: bool
) -> tuple[str | None, str | list[object]] | None:
    """Extract ``(turn_id, content)`` for a claude record, or ``None`` to skip.

    The ``is_parent_assistant`` predicate must be computed by the caller via
    ``is_parent_assistant_record(record)`` so the canonical predicate call
    remains visible in ``iter_merged_assistant_turns``'s body — that visibility
    is enforced by ``tests/arch/test_subagent_filter_guard.py``.
    """
    if not is_parent_assistant:
        return None
    return _claude_record_content(record)


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


def _extract_tools(content: list[object]) -> list[str]:
    """Return the tool-use names from a content list (empty blocks dropped)."""
    return [
        str(block["name"])
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and isinstance(block.get("name"), str)
        and block["name"]
    ]


def _accumulate_turn(
    pending: OrderedDict[str, tuple[str, list[str]]],
    insertion_order: list[tuple[str, str]],
    no_id_turns: dict[str, AssistantTurn],
    no_id_counter: list[int],
    turn_id: str | None,
    timestamp: str,
    tools: list[str],
    cap: int,
) -> None:
    """Append one turn's contribution to the accumulating collections.

    A ``turn_id`` of ``None`` or empty string both route into the
    no-id bucket; non-empty values merge into the ordered ``pending`` map.
    """
    if turn_id:
        if turn_id in pending:
            existing_timestamp, existing_tools = pending[turn_id]
            pending[turn_id] = (existing_timestamp or timestamp, existing_tools + tools)
        else:
            pending[turn_id] = (timestamp, tools)
            insertion_order.append(("id", turn_id))
    else:
        key = str(no_id_counter[0])
        no_id_counter[0] += 1
        no_id_turns[key] = AssistantTurn(f"turn-{key}", timestamp, tuple(tools[:cap]))
        insertion_order.append(("no-id", key))


def _emit_pending_turns(
    pending: OrderedDict[str, tuple[str, list[str]]],
    no_id_turns: dict[str, AssistantTurn],
    insertion_order: list[tuple[str, str]],
    cap: int,
) -> Iterator[AssistantTurn]:
    """Yield accumulated turns in their first-seen transcript order."""
    for kind, key in insertion_order:
        if kind == "id":
            timestamp, tools = pending[key]
            yield AssistantTurn(key, timestamp, tuple(tools[:cap]))
        else:
            yield no_id_turns[key]


def _resolve_record_timestamp(record: dict[str, object]) -> str:
    """Return the record's ``timestamp`` as a string (empty when missing/wrong type)."""
    timestamp = record.get("timestamp", "")
    return timestamp if isinstance(timestamp, str) else ""


def _content_as_list(raw_content: str | list[object]) -> list[object]:
    """Normalize an extractor-returned content value to a list."""
    return raw_content if isinstance(raw_content, list) else []


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
    no_id_counter = [0]
    active_codex_turn_id = ""

    for record in _iter_transcript_records(text):
        if backend == "codex":
            turn_id, raw_content, active_codex_turn_id = _codex_record_content(
                record, active_codex_turn_id
            )
            if turn_id is None:
                continue
        else:
            # Canonical parent-assistant predicate must be invoked here, in the
            # iterator's own body, for the architectural guard to see it.
            claude_result = _claude_record_content_with_predicate(
                record, is_parent_assistant_record(record)
            )
            if claude_result is None:
                continue
            turn_id, raw_content = claude_result

        content = _content_as_list(raw_content)
        timestamp = _resolve_record_timestamp(record)
        tools = _extract_tools(content)
        _accumulate_turn(
            pending,
            insertion_order,
            no_id_turns,
            no_id_counter,
            turn_id,
            timestamp,
            tools,
            cap,
        )

    yield from _emit_pending_turns(pending, no_id_turns, insertion_order, cap)
