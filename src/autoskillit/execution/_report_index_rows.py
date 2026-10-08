"""Pure report-index row schema and derivation from report-walk items.

This module performs no I/O. Bump the schema version when a field is added,
removed, renamed, or changes type or meaning.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CANONICAL_ACCOUNTING_FIELDS,
    SerializedTokenMeasure,
    TokenMeasure,
    extract_skill_name,
    get_logger,
)
from autoskillit.execution._report_index_types import (
    REQUEST_KIND,
    SESSION_KIND,
    SUBAGENT_KIND,
    TOOL_KIND,
    TURN_KIND,
)
from autoskillit.execution.evidence.otlp_tokens import (
    CLAUDE_CODE_SCOPE_NAME,
    claude_request_usage,
    iter_scoped_log_records,
    record_attributes,
    unique_count_attribute,
    unique_flag_attribute,
    unique_float_attribute,
    unique_string_attribute,
)
from autoskillit.execution.evidence.report_walk import OTLP_WALK_KIND, SESSION_WALK_KIND, WalkItem
from autoskillit.execution.session.turn_usage import classify_token_measure

REPORT_INDEX_SCHEMA_VERSION: Final[int] = 3
UNKNOWN_SOURCE: Final[str] = "unknown"
_STRUCTURED_MEASURE_SESSION_VERSION = 14
_TURN_MEASURE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
_PARENT_CONTEXT_FIELDS = frozenset({"parent_prompt_tokens", "subagent_return_tokens"})

logger = get_logger(__name__)


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


def _token_usage_map(value: object) -> dict[str, SerializedTokenMeasure]:
    mapping = value if isinstance(value, Mapping) else {}
    measures: dict[str, SerializedTokenMeasure] = {}
    for field in CANONICAL_ACCOUNTING_FIELDS:
        raw = mapping.get(field)
        try:
            measures[field] = (
                TokenMeasure.from_dict(raw).to_dict()
                if isinstance(raw, dict)
                else (
                    TokenMeasure.observed(raw).to_dict()
                    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0
                    else TokenMeasure.unknown().to_dict()
                )
            )
        except ValueError:
            measures[field] = TokenMeasure.unknown().to_dict()
    return measures


def _serialized_measure(value: object) -> SerializedTokenMeasure:
    try:
        if isinstance(value, TokenMeasure):
            return value.to_dict()
        if isinstance(value, Mapping):
            return TokenMeasure.from_dict(dict(value)).to_dict()
    except ValueError:
        pass
    return TokenMeasure.unknown().to_dict()


def _parent_context_spans(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (tuple, list)):
        return []
    spans: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        field = _text(raw.get("field"))
        if field not in _PARENT_CONTEXT_FIELDS:
            continue
        spans.append(
            {
                "field": field,
                "measure": _serialized_measure(raw.get("measure")),
                "invocation_id": _text(raw.get("invocation_id")),
                "turn_id": _text(raw.get("turn_id")),
                "source_id": _text(raw.get("source_id")),
                "timestamp": _text(raw.get("timestamp")),
                "harness": _text(raw.get("harness")),
                "provider": _text(raw.get("provider")),
                "model": _text(raw.get("model")),
                "tokenizer_version": _text(raw.get("tokenizer_version")),
                "encoding": _text(raw.get("encoding")),
                "reason": _text(raw.get("reason")),
            }
        )
    return spans


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
        "turn_usage_state": _text,
        "turn_usage_reason": _text,
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
        "child_id": _text,
        "native_parent_session_id": _text,
        "parent_session_key": _text,
        "role": _text,
        "actor_level": _text,
        "provider": _pair_text,
        "skill": _text,
        "recipe": _text,
        "step": _text,
        "level": _text,
        "token_usage": _token_usage_map,
        "tool_counts": _count_map,
        "transcript_state": _text,
        "usage_state": _text,
        "parent_context_spans": _parent_context_spans,
    },
    TURN_KIND: {
        "schema_version": _count,
        "time_ms": _count,
        "session_id": _text,
        "source_id": _text,
        "session_key": _text,
        "ordinal": _count,
        "request_id": _text,
        "message_id": _text,
        "harness": _pair_text,
        "provider": _pair_text,
        "model": _text,
        "skill": _text,
        "recipe": _text,
        "step": _text,
        "level": _text,
        **{field: _serialized_measure for field in _TURN_MEASURE_FIELDS},
        "context_window_tokens": _count,
        "context_fraction": _number,
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
        logger.debug(
            "report_index_token_measure_classification_failed",
            extra={"harness": harness, "provider": provider, "field": field},
        )
        return TokenMeasure.unknown().to_dict()


def rows_for_walk_item(item: WalkItem) -> list[dict[str, Any]]:
    if item.record is None or item.source_id is None:
        return []
    if item.kind == SESSION_WALK_KIND:
        session = _session_row(item.source_id, item.record)
        return [
            session,
            *_turn_rows(item.source_id, item.record, session),
            *_native_subagent_rows(item.source_id, item.record, session),
        ]
    if item.kind == OTLP_WALK_KIND:
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
        "turn_usage_state": _text(record.get("turn_usage_state")),
        "turn_usage_reason": _text(record.get("turn_usage_reason")),
    }


def _turn_rows(
    source_id: str, record: dict[str, Any], parent: Mapping[str, Any]
) -> list[dict[str, Any]]:
    raw_rows = record.get("turn_usage_rows")
    if not isinstance(raw_rows, (tuple, list)):
        return []
    rows: list[dict[str, Any]] = []
    for ordinal, turn in enumerate(raw_rows):
        if not isinstance(turn, Mapping):
            continue
        rows.append(
            {
                "schema_version": REPORT_INDEX_SCHEMA_VERSION,
                "kind": TURN_KIND,
                "key": f"{source_id}:turn:{ordinal}",
                "session_id": parent["session_id"],
                "time_ms": _iso_to_ms(turn.get("timestamp")),
                "source_id": source_id,
                "session_key": source_id,
                "ordinal": ordinal,
                "request_id": _text(turn.get("request_id")),
                "message_id": _text(turn.get("message_id")),
                "harness": _pair_text(turn.get("backend")),
                "provider": _pair_text(turn.get("provider_used")),
                "model": _text(turn.get("model")),
                "skill": parent["skill"],
                "recipe": parent["recipe"],
                "step": parent["step"],
                "level": parent["level"],
                **{field: _serialized_measure(turn.get(field)) for field in _TURN_MEASURE_FIELDS},
                "context_window_tokens": _count(turn.get("context_window_tokens")),
                "context_fraction": _number(turn.get("context_fraction")),
            }
        )
    return rows


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
    return (parsed - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)


def _skill_name(value: object) -> str | None:
    command = _text(value)
    if command is None:
        return None
    if command.startswith("$"):
        command = "/" + command[1:]
    return extract_skill_name(command)


def _attribution_skill(value: object) -> str | None:
    skill = _text(value)
    if skill is None:
        return None
    return _skill_name(skill) if skill.startswith(("/", "$")) else skill


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


def _native_subagent_rows(
    key: str,
    record: dict[str, Any],
    parent: Mapping[str, Any],
) -> list[dict[str, Any]]:
    source_row = record.get("row")
    outcomes = record.get("child_outcomes")
    if not isinstance(source_row, dict) or not isinstance(outcomes, (tuple, list)):
        return []
    native_parent_id = _text(source_row.get("session_id"))
    parent_key = _text(source_row.get("dir_name")) or key
    if native_parent_id is None:
        return []

    rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for child in outcomes:
        if not isinstance(child, Mapping):
            continue
        child_id = _text(child.get("child_id"))
        role = _text(child.get("role"))
        backend = _text(child.get("backend"))
        expected_source = {
            "claude_code": "transcript_metadata",
            "codex": "codex_rollout_metadata",
        }.get(backend or "")
        if (
            child_id is None
            or role is None
            or expected_source is None
            or child.get("evidence_source") != expected_source
            or child.get("parent_session_id") != native_parent_id
            or child.get("parent_session_key") not in (None, parent_key)
        ):
            continue
        child_key = f"{parent_key}:native:{backend}:{native_parent_id}:{child_id}"
        if child_key in seen_keys:
            continue
        seen_keys.add(child_key)

        effective_provider = _text(child.get("effective_provider"))
        if effective_provider is not None and effective_provider.casefold() == UNKNOWN_SOURCE:
            effective_provider = None
        provider = _pair_text(effective_provider)
        token_usage = (
            _token_usage_map(child.get("token_usage"))
            if effective_provider is not None
            else _unknown_child_usage_map()
        )
        if child.get("usage_state") == "unknown":
            token_usage = _unknown_child_usage_map()

        parent_skill = parent["skill"]
        attribution_skill = _attribution_skill(child.get("attribution_skill"))
        skill = (
            parent_skill
            if parent["level"] in ("skill", "orchestrator")
            and parent_skill is not None
            and attribution_skill == parent_skill
            else None
        )
        transcript_state = _text(child.get("transcript_state")) or "unknown"
        tool_counts = (
            _count_map(child.get("tool_counts")) if transcript_state == "observed" else None
        )
        rows.append(
            {
                "schema_version": REPORT_INDEX_SCHEMA_VERSION,
                "kind": SUBAGENT_KIND,
                "key": child_key,
                "session_id": native_parent_id,
                "time_ms": parent["time_ms"],
                "harness": parent["harness"],
                "agent_type": None,
                "model": _text(child.get("effective_model")),
                "final_model": None,
                "model_swapped": None,
                "event_sequence": None,
                "child_id": child_id,
                "native_parent_session_id": native_parent_id,
                "parent_session_key": parent_key,
                "role": role,
                "actor_level": "L0",
                "provider": provider,
                "skill": skill,
                "recipe": parent["recipe"],
                "step": parent["step"],
                "level": parent["level"],
                "token_usage": token_usage,
                "tool_counts": tool_counts,
                "transcript_state": transcript_state,
                "usage_state": _text(child.get("usage_state")) or "unknown",
                "parent_context_spans": _parent_context_spans(child.get("parent_context_spans")),
            }
        )
    rows.sort(key=lambda row: row["key"])
    return rows


def _unknown_child_usage_map() -> dict[str, SerializedTokenMeasure]:
    return {field: TokenMeasure.unknown().to_dict() for field in CANONICAL_ACCOUNTING_FIELDS}


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
        "event_sequence": unique_count_attribute(attributes, "event.sequence"),
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
        "event_sequence": unique_count_attribute(attributes, "event.sequence"),
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
        "event_sequence": unique_count_attribute(attributes, "event.sequence"),
        "child_id": None,
        "native_parent_session_id": None,
        "parent_session_key": None,
        "role": None,
        "actor_level": None,
        "provider": UNKNOWN_SOURCE,
        "skill": None,
        "recipe": None,
        "step": None,
        "level": None,
        "token_usage": _unknown_child_usage_map(),
        "tool_counts": None,
        "transcript_state": None,
        "usage_state": None,
        "parent_context_spans": [
            {
                "field": field,
                "measure": TokenMeasure.unavailable().to_dict(),
                "invocation_id": None,
                "turn_id": None,
                "source_id": position_key,
                "timestamp": None,
                "harness": AGENT_BACKEND_CLAUDE_CODE,
                "provider": None,
                "model": None,
                "tokenizer_version": None,
                "encoding": None,
                "reason": "parent_invocation_unverified",
            }
            for field in ("parent_prompt_tokens", "subagent_return_tokens")
        ],
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
