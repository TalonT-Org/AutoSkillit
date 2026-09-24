"""Shared builders for report-index tests.

Centralizes the OTLP attribute encoding, log-record builder, session-row
scaffold, and Claude Code scope name so the parallel test modules do not
each maintain their own near-identical helpers.
"""

from __future__ import annotations

from typing import Any

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
