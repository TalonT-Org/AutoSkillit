"""Pure report-index row schema and derivation from report-walk items.

This module performs no I/O. Bump the schema version when a field is added,
removed, renamed, or changes type or meaning.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Final, TypedDict

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CANONICAL_ACCOUNTING_FIELDS,
    SerializedTokenMeasure,
    TokenMeasure,
    extract_skill_name,
)
from autoskillit.execution.evidence._otlp_tokens import (
    CLAUDE_CODE_SCOPE_NAME,
    claude_request_usage,
    iter_scoped_log_records,
    record_attributes,
    unique_count_attribute,
    unique_flag_attribute,
    unique_float_attribute,
    unique_string_attribute,
)
from autoskillit.execution.evidence.report_walk import WalkItem
from autoskillit.execution.session._turn_usage import classify_token_measure

REPORT_INDEX_SCHEMA_VERSION: Final[int] = 1
SESSION_KIND: Final[str] = "session"
REQUEST_KIND: Final[str] = "request"
TOOL_KIND: Final[str] = "tool"
SUBAGENT_KIND: Final[str] = "subagent"
UNKNOWN_SOURCE: Final[str] = "unknown"
_STRUCTURED_MEASURE_SESSION_VERSION = 14
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ReportRowBase(TypedDict):
    """Common fields carried by each report-index fact row."""

    schema_version: int
    kind: str
    key: str
    session_id: str | None
    time_ms: int | None


class ReportSessionRow(ReportRowBase):
    """Session facts derived from one session or archive walk item.

    The provider is persisted verbatim from ``provider_used``; token
    classification casefolds it only for the source-pair lookup.
    """

    harness: str
    provider: str
    model: str | None
    skill: str | None
    recipe: str | None
    step: str | None
    level: str | None
    kitchen_id: str | None
    order_id: str | None
    dispatch_id: str | None
    campaign_id: str | None
    caller_session_id: str | None
    parent_session_id: str | None
    success: bool | None
    subtype: str | None
    adjudication_reason: str | None
    adjudication_subtype: str | None
    duration_seconds: float | None
    input_tokens: SerializedTokenMeasure
    output_tokens: SerializedTokenMeasure
    cache_read_tokens: SerializedTokenMeasure
    cache_write_tokens: SerializedTokenMeasure
    assistant_turn_count: int | None
    tool_counts: dict[str, int] | None


class ReportRequestRow(ReportRowBase):
    """Request facts derived from a Claude Code ``api_request`` log record."""

    harness: str
    request_id: str
    agent_name: str | None
    query_source: str | None
    model: str | None
    event_sequence: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cost_usd: float | None
    duration_ms: int | None


class ReportToolRow(ReportRowBase):
    """Tool facts derived from a Claude Code ``tool_result`` log record."""

    harness: str
    agent_name: str | None
    tool_name: str | None
    tool_use_id: str | None
    error_type: str | None
    success: bool | None
    duration_ms: int | None
    tool_input_size_bytes: int | None
    tool_result_size_bytes: int | None
    event_sequence: int | None


class ReportSubagentRow(ReportRowBase):
    """Subagent facts derived from a ``subagent_completed`` log record."""

    harness: str
    agent_type: str | None
    model: str | None
    final_model: str | None
    model_swapped: bool | None
    event_sequence: int | None


REPORT_ROW_TYPES: Mapping[str, type] = MappingProxyType(
    {
        SESSION_KIND: ReportSessionRow,
        REQUEST_KIND: ReportRequestRow,
        TOOL_KIND: ReportToolRow,
        SUBAGENT_KIND: ReportSubagentRow,
    }
)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _count_map(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    counts: dict[str, int] = {}
    for key, raw_count in value.items():
        count = _count(raw_count)
        if isinstance(key, str) and count is not None:
            counts[key] = count
    return counts


def _pair_text(value: object) -> str:
    return _text(value) or UNKNOWN_SOURCE


def _raw_measure(value: object) -> object:
    if isinstance(value, dict):
        return value
    return _count(value)


_ROW_FIELDS: dict[str, dict[str, Callable[[object], object]]] = {
    SESSION_KIND: {
        "schema_version": _count,
        "time_ms": _count,
        "session_id": _text,
        "harness": _pair_text,
        "provider": _pair_text,
        **{
            field: _text
            for field in (
                "model",
                "skill",
                "recipe",
                "step",
                "level",
                "kitchen_id",
                "order_id",
                "dispatch_id",
                "campaign_id",
                "caller_session_id",
                "parent_session_id",
                "subtype",
                "adjudication_reason",
                "adjudication_subtype",
            )
        },
        "success": _flag,
        "duration_seconds": _number,
        **{field: _raw_measure for field in CANONICAL_ACCOUNTING_FIELDS},
        "assistant_turn_count": _count,
        "tool_counts": _count_map,
    },
    REQUEST_KIND: {
        "schema_version": _count,
        "time_ms": _count,
        "session_id": _text,
        "harness": _pair_text,
        "request_id": _text,
        "agent_name": _text,
        "query_source": _text,
        "model": _text,
        "event_sequence": _count,
        **{field: _raw_measure for field in CANONICAL_ACCOUNTING_FIELDS},
        "cost_usd": _number,
        "duration_ms": _count,
    },
    TOOL_KIND: {
        "schema_version": _count,
        "time_ms": _count,
        "session_id": _text,
        "harness": _pair_text,
        "agent_name": _text,
        "tool_name": _text,
        "tool_use_id": _text,
        "error_type": _text,
        "success": _flag,
        "duration_ms": _count,
        "tool_input_size_bytes": _count,
        "tool_result_size_bytes": _count,
        "event_sequence": _count,
    },
    SUBAGENT_KIND: {
        "schema_version": _count,
        "time_ms": _count,
        "session_id": _text,
        "harness": _pair_text,
        "agent_type": _text,
        "model": _text,
        "final_model": _text,
        "model_swapped": _flag,
        "event_sequence": _count,
    },
}


def normalize_report_row(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    key = raw.get("key")
    if not isinstance(kind, str) or kind not in _ROW_FIELDS:
        return None
    if not isinstance(key, str) or not key:
        return None
    return {
        "kind": kind,
        "key": key,
        **{name: decode(raw.get(name)) for name, decode in _ROW_FIELDS[kind].items()},
    }


def resolve_token_measure(
    harness: str, provider: str, field: str, raw: object
) -> SerializedTokenMeasure:
    try:
        return classify_token_measure(harness, provider, field, raw).to_dict()
    except ValueError:
        return TokenMeasure.unknown().to_dict()


def rows_for_walk_item(item: WalkItem) -> list[dict[str, Any]]:
    if item.record is None or item.source_id is None:
        return []
    if item.kind == SESSION_KIND:
        return [_session_row(item.source_id, item.record)]
    if item.kind == "otlp":
        return _otlp_rows(item.source_id, item.record)
    return []


def _session_row(key: str, record: dict[str, Any]) -> dict[str, Any]:
    row = record["row"]
    harness = _pair_text(row.get("backend"))
    provider = _pair_text(row.get("provider_used"))
    verdict = row.get("adjudication_verdict")
    if not isinstance(verdict, dict):
        verdict = {}
    measures = {
        field: resolve_token_measure(harness, provider, field, _session_source_measure(row, field))
        for field in CANONICAL_ACCOUNTING_FIELDS
    }
    return {
        "schema_version": REPORT_INDEX_SCHEMA_VERSION,
        "kind": SESSION_KIND,
        "key": key,
        "session_id": _text(row.get("session_id")),
        "time_ms": _iso_to_ms(row.get("timestamp")),
        "harness": harness,
        "provider": provider,
        "model": _text(row.get("model_identifier")),
        "skill": _skill_name(row.get("skill_command")),
        "recipe": _text(row.get("recipe_name")),
        "step": _text(row.get("step_name")),
        "level": _text(row.get("session_type")),
        "kitchen_id": _text(row.get("kitchen_id")),
        "order_id": _text(row.get("order_id")),
        "dispatch_id": _text(row.get("dispatch_id")),
        "campaign_id": _text(row.get("campaign_id")),
        "caller_session_id": _text(row.get("caller_session_id")),
        "parent_session_id": _text(row.get("parent_session_id")),
        "success": _flag(row.get("success")),
        "subtype": _text(row.get("subtype")),
        "adjudication_reason": _text(verdict.get("reason_kind")),
        "adjudication_subtype": _text(verdict.get("subtype")),
        "duration_seconds": _number(row.get("duration_seconds")),
        **measures,
        "assistant_turn_count": _count(record.get("assistant_turn_count")),
        "tool_counts": _tool_counts(record),
    }


def _session_source_measure(row: dict[str, Any], field: str) -> object:
    value = row.get(field)
    version = _count(row.get("schema_version"))
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value == 0
        and (version is None or version < _STRUCTURED_MEASURE_SESSION_VERSION)
    ):
        return None
    return value


def _iso_to_ms(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed - _EPOCH) // timedelta(milliseconds=1)


def _skill_name(value: object) -> str | None:
    command = _text(value)
    if command is None:
        return None
    if command.startswith("$"):
        command = "/" + command[1:]
    return extract_skill_name(command)


def _tool_counts(record: dict[str, Any]) -> dict[str, int] | None:
    if record.get("transcripts_available") is not True:
        return None
    turns = record.get("assistant_turns")
    counts: Counter[str] = Counter()
    if isinstance(turns, list):
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            names = turn.get("tool_names")
            if isinstance(names, list):
                counts.update(name for name in names if isinstance(name, str))
    return dict(sorted(counts.items()))


def _otlp_rows(source_id: str, record: dict[str, Any]) -> list[dict[str, Any]]:
    if record.get("signal") != "logs":
        return []
    rows: list[dict[str, Any]] = []
    for ordinal, (_, log_record) in enumerate(
        iter_scoped_log_records(record.get("payload"), (CLAUDE_CODE_SCOPE_NAME,))
    ):
        attributes = record_attributes(log_record)
        if attributes is None:
            continue
        session_id = unique_string_attribute(attributes, "session.id")
        if session_id is None:
            continue
        event_name = unique_string_attribute(attributes, "event.name")
        if event_name is None:
            continue
        builder = _CLAUDE_EVENT_ROWS.get(event_name)
        if builder is None:
            continue
        fields = builder(attributes, session_id, f"{source_id}#{ordinal}")
        if fields is None:
            continue
        rows.append(
            {
                "schema_version": REPORT_INDEX_SCHEMA_VERSION,
                "session_id": session_id,
                "time_ms": _log_time_ms(log_record),
                **fields,
            }
        )
    return rows


def _request_fields(
    attributes: list[object], session_id: str, _position_key: str
) -> dict[str, Any] | None:
    request_id = unique_string_attribute(attributes, "request_id")
    if request_id is None:
        return None
    return {
        "kind": REQUEST_KIND,
        "key": f"{session_id}:{request_id}",
        "harness": AGENT_BACKEND_CLAUDE_CODE,
        "request_id": request_id,
        "agent_name": unique_string_attribute(attributes, "agent.name"),
        "query_source": unique_string_attribute(attributes, "query_source"),
        "model": unique_string_attribute(attributes, "model"),
        "event_sequence": unique_count_attribute(attributes, "event_sequence"),
        **claude_request_usage(attributes),
        "cost_usd": unique_float_attribute(attributes, "cost_usd"),
        "duration_ms": unique_count_attribute(attributes, "duration_ms"),
    }


def _tool_fields(attributes: list[object], session_id: str, position_key: str) -> dict[str, Any]:
    tool_use_id = unique_string_attribute(attributes, "tool_use_id")
    key = f"{session_id}:{tool_use_id}" if tool_use_id is not None else position_key
    return {
        "kind": TOOL_KIND,
        "key": key,
        "harness": AGENT_BACKEND_CLAUDE_CODE,
        "agent_name": unique_string_attribute(attributes, "agent.name"),
        "tool_name": unique_string_attribute(attributes, "tool_name"),
        "tool_use_id": tool_use_id,
        "error_type": unique_string_attribute(attributes, "error_type"),
        "success": unique_flag_attribute(attributes, "success"),
        "duration_ms": unique_count_attribute(attributes, "duration_ms"),
        "tool_input_size_bytes": unique_count_attribute(attributes, "tool_input_size_bytes"),
        "tool_result_size_bytes": unique_count_attribute(attributes, "tool_result_size_bytes"),
        "event_sequence": unique_count_attribute(attributes, "event_sequence"),
    }


def _subagent_fields(
    attributes: list[object], _session_id: str, position_key: str
) -> dict[str, Any]:
    return {
        "kind": SUBAGENT_KIND,
        "key": position_key,
        "harness": AGENT_BACKEND_CLAUDE_CODE,
        "agent_type": unique_string_attribute(attributes, "agent_type"),
        "model": unique_string_attribute(attributes, "model"),
        "final_model": unique_string_attribute(attributes, "final_model"),
        "model_swapped": unique_flag_attribute(attributes, "model_swapped"),
        "event_sequence": unique_count_attribute(attributes, "event_sequence"),
    }


_CLAUDE_EVENT_ROWS: Mapping[str, Callable[[list[object], str, str], dict[str, Any] | None]] = {
    "api_request": _request_fields,
    "tool_result": _tool_fields,
    "subagent_completed": _subagent_fields,
}


def _log_time_ms(log_record: object) -> int | None:
    if not isinstance(log_record, dict):
        return None
    for field in ("timeUnixNano", "observedTimeUnixNano"):
        value = log_record.get(field)
        timestamp = _timestamp_nanos(value)
        if timestamp is not None:
            return timestamp // 10**6
    return None


def _timestamp_nanos(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None
