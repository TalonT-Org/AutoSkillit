"""Prompt and strict NDJSON result protocol for the sterile evidence reader."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from autoskillit.core import DIRECT_PREFIX, AgentDef, canonical_reader_tools_to_bare
from autoskillit.execution.backends._codex_parse import CodexStreamParser
from autoskillit.execution.evidence._reader_contract import (
    _RESULT_KEYS,
    CITATION_LOCATION_KEYS,
    EvidenceCitation,
    EvidenceReaderLaunchError,
    EvidenceReaderLaunchResult,
    EvidenceReaderResultStatus,
)


def _prompt(
    definition: AgentDef,
    prompt: str,
    *,
    canary: str,
    scope: str,
    snapshot: str,
) -> str:
    return (
        f"{definition.body}\n\n"
        f"Your first MCP call must be {DIRECT_PREFIX}read_authorized_artifact. Use "
        f"{DIRECT_PREFIX}get_authorized_artifact_page only when its continuation is non-null. "
        "Do not list MCP "
        "resources, templates, or tools. Operate only through those authorized evidence broker "
        "tools. Never invoke commands, "
        "file operations, delegation, web search, permissions, or any unlisted tool. For every "
        "evidence item, copy the broker's citation_id and all four byte/line location values "
        "exactly; do not adjust them per field. Return exactly one compact JSON object matching "
        "the role's Completion shape, adding the "
        f'top-level field "canary":{json.dumps(canary)}. The authorized_scope must be '
        f"{json.dumps(scope)}, snapshot must be {json.dumps(snapshot)}, role must be "
        f"{json.dumps(definition.name)}. Set child_identity.thread_id to any non-empty "
        "placeholder; "
        f"the launcher replaces it with the observed Codex thread identity. Task: {prompt}"
    )


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or len(value) > 1_000_000:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _observed_citations(value: Any) -> dict[str, tuple[int, int, int, int]]:
    found: dict[str, tuple[int, int, int, int]] = {}

    def visit(item: Any) -> None:
        decoded = _json_object(item)
        if decoded is not None and decoded is not item:
            visit(decoded)
            return
        if isinstance(item, dict):
            citation_id = item.get("citation_id")
            if isinstance(citation_id, str):
                location = item.get("location")
                source = location if isinstance(location, dict) else item
                fields = tuple(source.get(name) for name in CITATION_LOCATION_KEYS)
                if all(isinstance(field, int) and not isinstance(field, bool) for field in fields):
                    normalized_fields = cast(tuple[int, int, int, int], fields)
                    if citation_id in found and found[citation_id] != normalized_fields:
                        raise EvidenceReaderLaunchError("citation_invalid")
                    found[citation_id] = normalized_fields
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return found


def _validate_result_payload(
    result_message: str,
    *,
    definition: AgentDef,
    canary: str,
    scope: str,
    snapshot: str,
    requested_fields: tuple[str, ...],
    max_result_bytes: int,
    observed_citations: Mapping[str, tuple[int, int, int, int]],
    thread_id: str,
) -> tuple[str, tuple[EvidenceCitation, ...], EvidenceReaderResultStatus]:
    result_bytes = result_message.encode("utf-8")
    if len(result_bytes) > max_result_bytes:
        raise EvidenceReaderLaunchError("result_limit_exceeded")
    try:
        payload = json.loads(result_bytes)
    except json.JSONDecodeError as exc:
        raise EvidenceReaderLaunchError("result_schema_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != _RESULT_KEYS:
        raise EvidenceReaderLaunchError("result_schema_invalid")
    raw_status = payload.get("status")
    if not isinstance(raw_status, str):
        raise EvidenceReaderLaunchError("result_schema_invalid")
    try:
        status = EvidenceReaderResultStatus(raw_status)
    except (TypeError, ValueError) as exc:
        raise EvidenceReaderLaunchError("result_schema_invalid") from exc
    child = payload.get("child_identity")
    if (
        payload.get("canary") != canary
        or payload.get("role") != definition.name
        or payload.get("authorized_scope") != scope
        or payload.get("snapshot") != snapshot
        or not isinstance(child, dict)
        or set(child) != {"thread_id"}
        or not isinstance(child.get("thread_id"), str)
        or not child["thread_id"]
        or type(payload.get("complete")) is not bool
        or type(payload.get("truncated")) is not bool
        or not isinstance(payload.get("stop_reason"), str)
        or not payload["stop_reason"]
        or not isinstance(payload.get("evidence"), list)
        or not isinstance(payload.get("coverage_gaps"), list)
    ):
        raise EvidenceReaderLaunchError("result_schema_invalid")
    citations: list[EvidenceCitation] = []
    for evidence in payload["evidence"]:
        if not isinstance(evidence, dict) or set(evidence) != {
            "field",
            "value",
            "representation",
            "citation_id",
            "location",
        }:
            raise EvidenceReaderLaunchError("result_schema_invalid")
        raw_citation_id = evidence.get("citation_id")
        raw_location = evidence.get("location")
        if (
            not isinstance(raw_citation_id, str)
            or not raw_citation_id
            or not isinstance(raw_location, dict)
        ):
            raise EvidenceReaderLaunchError("citation_invalid")
        fields = tuple(raw_location.get(name) for name in CITATION_LOCATION_KEYS)
        if (
            evidence.get("representation") not in {"literal", "summary"}
            or not isinstance(evidence.get("field"), str)
            or not evidence["field"]
            or not isinstance(evidence.get("value"), str)
            or not all(isinstance(field, int) and not isinstance(field, bool) for field in fields)
        ):
            raise EvidenceReaderLaunchError("citation_invalid")
        location_fields = cast(tuple[int, int, int, int], fields)
        receipt_location = observed_citations.get(raw_citation_id)
        if (
            receipt_location is None
            or location_fields[0] < 0
            or location_fields[1] < location_fields[0]
            or location_fields[2] < 1
            or location_fields[3] < location_fields[2]
            or location_fields != receipt_location
        ):
            raise EvidenceReaderLaunchError("citation_invalid")
        citations.append(EvidenceCitation(raw_citation_id, *location_fields))
    if any(
        not isinstance(gap, dict)
        or set(gap) != {"field", "reason"}
        or not isinstance(gap.get("field"), str)
        or not gap["field"]
        or not isinstance(gap.get("reason"), str)
        or not gap["reason"]
        for gap in payload["coverage_gaps"]
    ):
        raise EvidenceReaderLaunchError("result_schema_invalid")
    evidence_fields = tuple(item["field"] for item in payload["evidence"])
    gap_fields = tuple(item["field"] for item in payload["coverage_gaps"])
    if (
        len(requested_fields) != len(set(requested_fields))
        or len(evidence_fields) != len(set(evidence_fields))
        or len(gap_fields) != len(set(gap_fields))
        or set(evidence_fields) & set(gap_fields)
        or set(evidence_fields) | set(gap_fields) != set(requested_fields)
    ):
        raise EvidenceReaderLaunchError("result_partition_invalid")
    complete = payload["complete"]
    truncated = payload["truncated"]
    stop_reason = payload["stop_reason"]
    state_valid = {
        EvidenceReaderResultStatus.ANSWERED: (
            bool(evidence_fields)
            and not gap_fields
            and complete
            and not truncated
            and stop_reason == "requested fields covered"
        ),
        EvidenceReaderResultStatus.PARTIAL: (
            bool(evidence_fields)
            and bool(gap_fields)
            and complete
            and not truncated
            and stop_reason in {"artifact exhausted", "concrete blocker"}
        ),
        EvidenceReaderResultStatus.BLOCKED: (
            not evidence_fields
            and bool(gap_fields)
            and complete
            and not truncated
            and stop_reason == "concrete blocker"
        ),
    }
    if not state_valid[status]:
        raise EvidenceReaderLaunchError("result_state_invalid")
    payload["child_identity"] = {"thread_id": thread_id}
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return payload_json, tuple(citations), status


def _validate_stream(
    output: bytes,
    *,
    definition: AgentDef,
    allowed_tools: tuple[str, ...],
    canary: str,
    scope: str,
    snapshot: str,
    requested_fields: tuple[str, ...],
    max_result_bytes: int,
) -> EvidenceReaderLaunchResult:
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EvidenceReaderLaunchError("stream_invalid") from exc
    parser = CodexStreamParser()
    thread_ids: list[str] = []
    terminal = 0
    result_messages: list[str] = []
    observed: dict[str, tuple[int, int, int, int]] = {}
    started_mcp_calls: dict[str, str] = {}
    completed_mcp_call_ids: set[str] = set()
    completed_mcp_tools: list[str] = []
    successful_mcp_tools: list[str] = []
    definition_bare_tools = canonical_reader_tools_to_bare(definition.reader_tools)
    tool_aliases = {
        **dict(zip(definition.reader_tools, definition_bare_tools, strict=True)),
        **{tool: tool for tool in allowed_tools},
    }
    allowed_tool_names = frozenset(tool_aliases)
    allowed_events = {
        "thread.started",
        "turn.started",
        "item.started",
        "item.updated",
        "item.completed",
        "turn.completed",
    }
    allowed_items = {"reasoning", "todo_list", "mcp_tool_call", "agent_message", "message"}
    for raw_line in text.splitlines():
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise EvidenceReaderLaunchError("stream_invalid") from exc
        if not isinstance(raw, dict) or raw.get("type") not in allowed_events:
            raise EvidenceReaderLaunchError("stream_shape_forbidden")
        event = parser.parse_line(raw_line)
        if event is None:
            raise EvidenceReaderLaunchError("stream_shape_forbidden")
        if raw["type"] == "thread.started":
            thread_id = raw.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                raise EvidenceReaderLaunchError("child_identity_invalid")
            thread_ids.append(thread_id)
        elif raw["type"] == "turn.completed":
            terminal += 1
        elif raw["type"] in {"item.started", "item.updated", "item.completed"}:
            item = raw.get("item")
            if not isinstance(item, dict) or item.get("type") not in allowed_items:
                raise EvidenceReaderLaunchError("forbidden_operation")
            if item["type"] == "mcp_tool_call":
                tool = item.get("tool_name", item.get("name", item.get("tool")))
                if tool not in allowed_tool_names:
                    raise EvidenceReaderLaunchError("tool_not_authorized")
                normalized_tool = tool_aliases[cast(str, tool)]
                call_id = item.get("id")
                if call_id is not None and (not isinstance(call_id, str) or not call_id):
                    raise EvidenceReaderLaunchError("stream_shape_forbidden")
                if raw["type"] == "item.started" and isinstance(call_id, str):
                    if call_id in started_mcp_calls:
                        raise EvidenceReaderLaunchError("stream_shape_forbidden")
                    started_mcp_calls[call_id] = normalized_tool
                if raw["type"] == "item.completed":
                    if isinstance(call_id, str) and call_id in completed_mcp_call_ids:
                        raise EvidenceReaderLaunchError("stream_shape_forbidden")
                    if (
                        item.get("status") not in {None, "completed", "success"}
                        or item.get("error") not in {None, ""}
                        or (
                            isinstance(call_id, str)
                            and (
                                call_id not in started_mcp_calls
                                or started_mcp_calls[call_id] != normalized_tool
                            )
                        )
                    ):
                        raise EvidenceReaderLaunchError("mcp_call_failed")
                    if isinstance(call_id, str):
                        completed_mcp_call_ids.add(call_id)
                    completed_mcp_tools.append(normalized_tool)
                    call_citations = _observed_citations(item)
                    if call_citations:
                        successful_mcp_tools.append(normalized_tool)
                    for citation_id, location in call_citations.items():
                        if citation_id in observed and observed[citation_id] != location:
                            raise EvidenceReaderLaunchError("citation_invalid")
                        observed[citation_id] = location
            elif item["type"] == "agent_message" and raw["type"] == "item.completed":
                if isinstance(item.get("text"), str):
                    result_messages.append(item["text"])
            elif item["type"] == "message" and raw["type"] == "item.completed":
                blocks = item.get("content")
                if not isinstance(blocks, list):
                    raise EvidenceReaderLaunchError("stream_shape_forbidden")
                result_messages.extend(
                    block["text"]
                    for block in blocks
                    if isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                )
    if parser.ndjson_unknown_event_count or parser.ndjson_unknown_item_count:
        raise EvidenceReaderLaunchError("stream_shape_forbidden")
    if len(thread_ids) != 1 or terminal != 1 or len(result_messages) != 1:
        raise EvidenceReaderLaunchError("terminal_result_invalid")
    initial_seen = False
    page_started = False
    sequence_invalid = not completed_mcp_tools
    for tool in successful_mcp_tools:
        if tool == "read_authorized_artifact":
            sequence_invalid = sequence_invalid or page_started
            initial_seen = True
        elif tool == "get_authorized_artifact_page":
            sequence_invalid = sequence_invalid or not initial_seen
            page_started = True
    if set(started_mcp_calls) - completed_mcp_call_ids or sequence_invalid:
        raise EvidenceReaderLaunchError("mcp_call_sequence_invalid")
    payload_json, citations, status = _validate_result_payload(
        result_messages[0],
        definition=definition,
        canary=canary,
        scope=scope,
        snapshot=snapshot,
        requested_fields=requested_fields,
        max_result_bytes=max_result_bytes,
        observed_citations=observed,
        thread_id=thread_ids[0],
    )
    return EvidenceReaderLaunchResult(
        status=status,
        role=definition.name,
        authorized_scope=scope,
        snapshot_digest=snapshot,
        thread_id=thread_ids[0],
        citations=citations,
        payload_json=payload_json,
    )
