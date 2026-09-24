"""Bounded token observations projected from native OTLP log records."""

from __future__ import annotations

import math
from collections.abc import Collection, Iterator, Sequence
from typing import Any

from autoskillit.core import CANONICAL_ACCOUNTING_FIELDS, TokenMeasure, get_logger
from autoskillit.execution.session._turn_usage import classify_token_measure

__all__ = [
    "TokenObservation",
    "aggregate_token_observations",
    "has_attribute",
    "project_token_observations",
    "record_attributes",
    "unique_bool_attribute",
    "unique_string_attribute",
]

TokenObservation = tuple[str, str, dict[str, int | None]]

_MAX_TOKEN_OBSERVATIONS_PER_PAYLOAD = 128

# Scope name emitted by Claude Code's native OTLP logs. Defined once here so
# the iterator filter and the overflow-debug log agree on the literal.
CLAUDE_CODE_SCOPE_NAME = "com.anthropic.claude_code.events"

logger = get_logger(__name__)


def record_attributes(record: object) -> list[object] | None:
    if not isinstance(record, dict):
        return None
    attributes = record.get("attributes")
    return attributes if isinstance(attributes, list) else None


def unique_string_attribute(attributes: list[object], key: str) -> str | None:
    value = _unique_attribute_value(attributes, key)
    if value is None:
        return None
    scalar = value.get("stringValue")
    return scalar if isinstance(scalar, str) and scalar else None


def unique_bool_attribute(attributes: list[object], key: str) -> bool | None:
    value = _unique_attribute_value(attributes, key)
    if value is None:
        return None
    scalar = value.get("boolValue")
    return scalar if isinstance(scalar, bool) else None


def _unique_attribute_value(attributes: list[object], *keys: str) -> dict[str, object] | None:
    matches = [item for item in attributes if isinstance(item, dict) and item.get("key") in keys]
    if len(matches) != 1:
        return None
    value = matches[0].get("value")
    return value if isinstance(value, dict) else None


def has_attribute(attributes: list[object], key: str) -> bool:
    return any(isinstance(item, dict) and item.get("key") == key for item in attributes)


def unique_count_attribute(attributes: list[object], *keys: str) -> int | None:
    value = _unique_attribute_value(attributes, *keys)
    if value is None:
        return None
    scalar = value.get("intValue", value.get("stringValue"))
    if isinstance(scalar, bool):
        return None
    if isinstance(scalar, int) and scalar >= 0:
        return scalar
    if isinstance(scalar, str) and scalar.isdecimal():
        return int(scalar)
    return None


def unique_float_attribute(attributes: list[object], key: str) -> float | None:
    value = _unique_attribute_value(attributes, key)
    if value is None:
        return None
    scalar = value.get("doubleValue", value.get("intValue", value.get("stringValue")))
    if isinstance(scalar, bool) or not isinstance(scalar, (int, float, str)):
        return None
    try:
        number = float(scalar)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def unique_flag_attribute(attributes: list[object], key: str) -> bool | None:
    value = _unique_attribute_value(attributes, key)
    if value is None:
        return None
    scalar = value.get("boolValue")
    if isinstance(scalar, bool):
        return scalar
    string_value = value.get("stringValue")
    if string_value == "true":
        return True
    if string_value == "false":
        return False
    return None


def claude_request_usage(attributes: list[object]) -> dict[str, int | None]:
    return {
        "input_tokens": unique_count_attribute(attributes, "input_tokens"),
        "output_tokens": unique_count_attribute(attributes, "output_tokens"),
        "cache_read_tokens": unique_count_attribute(attributes, "cache_read_tokens", "cacheRead"),
        "cache_write_tokens": unique_count_attribute(
            attributes, "cache_creation_tokens", "cacheCreation"
        ),
    }


def iter_scoped_log_records(
    payload: object, scope_names: Collection[str]
) -> Iterator[tuple[str, object]]:
    """Yield (scope_name, log_record) for native log records in the named scopes."""
    if not isinstance(payload, dict):
        return
    resource_logs = payload.get("resourceLogs")
    if not isinstance(resource_logs, list):
        return
    for resource_log in resource_logs:
        if not isinstance(resource_log, dict):
            continue
        yield from _resource_log_records(resource_log, scope_names)


def _resource_log_records(
    resource_log: dict[object, object], scope_names: Collection[str]
) -> Iterator[tuple[str, object]]:
    scope_logs = resource_log.get("scopeLogs")
    if not isinstance(scope_logs, list):
        return
    for scope_log in scope_logs:
        if not isinstance(scope_log, dict):
            continue
        scope = scope_log.get("scope")
        scope_name = scope.get("name") if isinstance(scope, dict) else None
        if not isinstance(scope_name, str) or scope_name not in scope_names:
            continue
        records = scope_log.get("logRecords")
        if isinstance(records, list):
            for record in records:
                yield scope_name, record


def project_token_observations(signal: str, payload: object) -> tuple[TokenObservation, ...]:
    """Project only request-correlated parent accounting from native Claude logs.

    Returns an empty tuple (never None) when the payload is not a logs
    signal or carries no valid observations; callers can iterate the
    result unconditionally.
    """
    if signal != "logs" or not isinstance(payload, dict):
        return ()
    observations: list[TokenObservation] = []
    for _, record in iter_scoped_log_records(payload, (CLAUDE_CODE_SCOPE_NAME,)):
        attributes = record_attributes(record)
        if attributes is None:
            continue
        observation = _project_token_observation(attributes)
        if observation is None:
            continue
        if len(observations) >= _MAX_TOKEN_OBSERVATIONS_PER_PAYLOAD:
            logger.debug(
                "token_observations_overflow",
                extra={
                    "scope": CLAUDE_CODE_SCOPE_NAME,
                    "limit": _MAX_TOKEN_OBSERVATIONS_PER_PAYLOAD,
                    "count": len(observations),
                },
            )
            return tuple(observations)
        observations.append(observation)
    return tuple(observations)


def _project_token_observation(attributes: list[object]) -> TokenObservation | None:
    if unique_string_attribute(attributes, "event.name") != "api_request":
        return None
    if unique_string_attribute(attributes, "query_source") != "sdk":
        return None
    if has_attribute(attributes, "agent.name"):
        return None
    session_id = unique_string_attribute(attributes, "session.id")
    request_id = unique_string_attribute(attributes, "request_id")
    if not session_id or not request_id:
        return None
    return session_id, request_id, claude_request_usage(attributes)


def aggregate_token_observations(
    observations: Sequence[dict[str, int | None]],
    backend: str,
    provider_used: str,
) -> dict[str, Any]:
    """Aggregate verified requests without combining source pairs or missing values."""
    fields = CANONICAL_ACCOUNTING_FIELDS
    unknowns = {field: TokenMeasure.unknown().to_dict() for field in fields}
    if not observations:
        return {
            "backend": backend,
            "provider_used": provider_used,
            **unknowns,
            "peak_context": TokenMeasure.unknown().to_dict(),
        }
    totals: dict[str, TokenMeasure] = {}
    peak: TokenMeasure | None = None
    for observation in observations:
        for field in fields:
            measure = classify_token_measure(backend, provider_used, field, observation.get(field))
            if field not in totals:
                totals[field] = measure
                continue
            totals[field] = TokenMeasure.combine_or_unknown(totals[field], measure)
        cache_read = classify_token_measure(
            backend, provider_used, "peak_context", observation.get("cache_read_tokens")
        )
        if peak is None:
            peak = cache_read
        else:
            peak = TokenMeasure.maximum_or_unknown(peak, cache_read)
    return {
        "backend": backend,
        "provider_used": provider_used,
        **{field: totals[field].to_dict() for field in fields},
        "peak_context": (peak or TokenMeasure.unknown()).to_dict(),
    }
