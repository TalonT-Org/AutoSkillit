"""Shared builders for report-index tests.

Centralizes the OTLP attribute encoding, log-record builder, session-row
scaffold, and Claude Code scope name so the parallel test modules do not
each maintain their own near-identical helpers.
"""

from __future__ import annotations

import json
from typing import Any

from autoskillit.core import TOKEN_USAGE_SCHEMA_VERSION, TURN_USAGE_SCHEMA_VERSION

CLAUDE_SCOPE: str = "com.anthropic.claude_code.events"


def _attrs(**values: object) -> list[dict[str, Any]]:
    """Encode keyword values as OTLP attribute entries (``{key, value}``)."""
    attributes: list[dict[str, Any]] = []
    for key, value in values.items():
        if isinstance(value, bool):
            encoded: dict[str, Any] = {"boolValue": value}
        elif isinstance(value, str):
            encoded = {"stringValue": value}
        elif isinstance(value, int):
            encoded = {"intValue": value}
        elif isinstance(value, float):
            encoded = {"doubleValue": value}
        else:
            raise TypeError(f"Unsupported test attribute value: {type(value).__name__}")
        attributes.append({"key": key, "value": encoded})
    return attributes


def otlp_log_record(
    event: str,
    session_id: str | None,
    *,
    time_ns: int | None = None,
    observed_ns: int | None = None,
    **attrs: object,
) -> dict[str, Any]:
    """Return one native OTLP log record with the requested attributes.

    The record's ``scope.name`` is intentionally omitted so tests that need
    it can pass it through the surrounding payload or assert on its absence.
    """
    attributes: dict[str, object] = {"event.name": event, **attrs}
    if session_id is not None:
        attributes["session.id"] = session_id
    record: dict[str, Any] = {"attributes": _attrs(**attributes)}
    if time_ns is not None:
        record["timeUnixNano"] = str(time_ns)
    if observed_ns is not None:
        record["observedTimeUnixNano"] = str(observed_ns)
    return record


def basic_session_row(dir_name: str, session_id: str, **fields: Any) -> dict[str, Any]:
    """Return one session row suitable for the report-walk session path."""
    session: dict[str, Any] = {
        "dir_name": dir_name,
        "session_id": session_id,
        "backend": "claude-code",
        "provider_used": "anthropic",
        "timestamp": "2020-01-01T00:00:00Z",
        **fields,
    }
    return session


def _jsonl(*records: dict[str, Any]) -> str:
    return "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)


def _encoding(name: str, *, merge_ab: bool = False) -> Any:
    import tiktoken

    ranks = {bytes((value,)): value for value in range(256)}
    if merge_ab:
        ranks[b"ab"] = 256
    return tiktoken.Encoding(
        name=name,
        pat_str=r"[\s\S]+",
        mergeable_ranks=ranks,
        special_tokens={"<|endoftext|>": 257 if merge_ab else 256},
    )


def _claude_call(
    *,
    child_id: str | None,
    tool_id: str,
    prompt: str,
    model: str = "gpt-known",
) -> dict[str, Any]:
    inputs: dict[str, str] = {"prompt": prompt}
    if child_id is not None:
        inputs["agent_id"] = child_id
    return {
        "type": "assistant",
        "uuid": f"record-{tool_id}",
        "timestamp": "2026-10-07T10:00:00Z",
        "requestId": f"turn-{tool_id}",
        "message": {
            "id": f"message-{tool_id}",
            "model": model,
            "content": [{"type": "tool_use", "id": tool_id, "name": "Task", "input": inputs}],
        },
    }


def _claude_result(*, tool_id: str, record_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": record_id,
        "timestamp": f"2026-10-07T10:00:0{record_id[-1]}Z",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": [{"type": "text", "text": text}],
                }
            ]
        },
    }


def _turn_usage_descriptor(
    *,
    count: int,
    filename: str | None = "turn_usage.jsonl",
    version: int = TURN_USAGE_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "schema_version": TOKEN_USAGE_SCHEMA_VERSION,
        "turn_usage_file": filename,
        "turn_usage_count": count,
        "turn_usage_schema_version": version,
    }
