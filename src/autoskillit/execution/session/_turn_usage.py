"""Shared construction and snapshot merging for per-request token rows."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    AGENT_BACKEND_CODEX,
    TokenMeasure,
    TurnTokenEntry,
    fast_dumps,
    fsync_directory,
    get_logger,
)

logger = get_logger(__name__)


def primary_model_identifier(token_usage: dict[str, Any] | None) -> str:
    """Return the aggregate bucket with the most output tokens."""
    if not token_usage:
        return ""
    model_breakdown = token_usage.get("model_breakdown", {})
    if not isinstance(model_breakdown, dict) or not model_breakdown:
        return ""

    def output_count(model: str) -> int:
        bucket = model_breakdown[model]
        if not isinstance(bucket, dict):
            return -1
        value = bucket.get("output_tokens")
        if isinstance(value, dict):
            try:
                measured = TokenMeasure.from_dict(value).value
            except ValueError:
                return -1
            return measured if measured is not None else -1
        observed = valid_token_count(value)
        return observed if observed is not None else -1

    return max(model_breakdown, key=output_count)


def resolve_session_label(step_name: str, dispatch_id: str) -> str:
    """Return the stable token/timing label for a completed session."""
    if step_name:
        return step_name
    if dispatch_id:
        return f"dispatch:{dispatch_id}"
    return "(ad-hoc)"


def first_parent_message_timestamps(
    text: str, is_parent_assistant_record: Callable[[dict[str, Any]], bool]
) -> dict[str, str]:
    """Map native parent message IDs to their first valid source timestamp."""
    timestamps: dict[str, str] = {}
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or not is_parent_assistant_record(record):
            continue
        message = record.get("message")
        timestamp = record.get("timestamp")
        if not isinstance(message, dict) or not isinstance(timestamp, str) or not timestamp:
            continue
        message_id = message.get("id")
        if isinstance(message_id, str) and message_id:
            timestamps.setdefault(message_id, timestamp)
    return timestamps


def write_turn_usage_sidecar(path: Path, rows: list[TurnTokenEntry]) -> bool:
    """Stream and atomically publish a complete JSONL turn ledger."""
    fd = -1
    temp_path: Path | None = None
    try:
        fd, raw_temp_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temp_path = Path(raw_temp_path)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            fd = -1
            for row in rows:
                output.write(fast_dumps(serialize_turn_token_entry(row), sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
        fsync_directory(path.parent)
        return True
    except (KeyError, OSError, TypeError, ValueError):
        logger.debug("turn_usage_sidecar_write_failed", path=str(path), exc_info=True)
        return False
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def valid_token_count(value: Any) -> int | None:
    """Return a provider counter only when it is an honest token count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


_ACCOUNTING_MEASURES = frozenset(
    {"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "peak_context"}
)
_NO_CACHE_WRITE_BACKENDS = frozenset({AGENT_BACKEND_CODEX})
_NO_CACHE_WRITE_PAIRS = frozenset({(AGENT_BACKEND_CLAUDE_CODE, "minimax")})
_CANONICAL_TO_LEGACY: dict[str, str | None] = {
    "input_tokens": None,
    "output_tokens": None,
    "cache_write_tokens": "cache_creation_input_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
}


def classify_token_measure(
    backend: str, provider_used: str, token_class: str, raw_value: object
) -> TokenMeasure:
    """Classify raw presence using the complete execution source pair."""
    if not backend or not provider_used:
        raise ValueError("Token observations require backend and provider_used")
    if token_class not in _ACCOUNTING_MEASURES:
        raise ValueError(f"Not an accounting token measure: {token_class}")
    if isinstance(raw_value, dict):
        return TokenMeasure.from_dict(raw_value)
    observed = valid_token_count(raw_value)
    if observed is not None:
        return TokenMeasure.observed(observed)
    if token_class == "cache_write_tokens" and (
        backend in _NO_CACHE_WRITE_BACKENDS
        or (backend, provider_used.casefold()) in _NO_CACHE_WRITE_PAIRS
    ):
        return TokenMeasure.unavailable()
    return TokenMeasure.unknown()


def merge_token_usage_measures(
    base: dict[str, object] | None,
    nudge: dict[str, object] | None,
) -> dict[str, object] | None:
    """Merge observations only inside the same backend/provider source pair."""
    if base is None:
        return nudge
    if nudge is None:
        return base
    backend = base.get("backend")
    provider_used = base.get("provider_used")
    if (
        not isinstance(backend, str)
        or not backend
        or not isinstance(provider_used, str)
        or not provider_used
    ):
        raise ValueError("Base token usage has no source pair")
    if (backend, provider_used) != (nudge.get("backend"), nudge.get("provider_used")):
        raise ValueError("Cannot merge token usage from different source pairs")
    merged = dict(base)
    for canonical, legacy in _CANONICAL_TO_LEGACY.items():
        b = base.get(canonical) if canonical in base else base.get(legacy) if legacy else None
        n = nudge.get(canonical) if canonical in nudge else nudge.get(legacy) if legacy else None
        left = classify_token_measure(backend, provider_used, canonical, b)
        right = classify_token_measure(backend, provider_used, canonical, n)
        merged[canonical] = TokenMeasure.combine_or_unknown(left, right).to_dict()
    left_peak = classify_token_measure(
        backend, provider_used, "peak_context", base.get("peak_context")
    )
    right_peak = classify_token_measure(
        backend, provider_used, "peak_context", nudge.get("peak_context")
    )
    merged["peak_context"] = TokenMeasure.maximum_or_unknown(left_peak, right_peak).to_dict()
    for legacy in _CANONICAL_TO_LEGACY.values():
        if legacy and legacy in merged:
            del merged[legacy]
    merged.pop("model_breakdown", None)
    return merged


def first_nonempty_string(*values: Any) -> str | None:
    """Return the first non-empty string from provider evidence."""
    return next((value for value in values if isinstance(value, str) and value), None)


def first_valid_token_count(usage: Mapping[str, Any], *fields: str) -> int | None:
    """Return the first valid token count from the named provider fields."""
    for field_name in fields:
        value = valid_token_count(usage.get(field_name))
        if value is not None:
            return value
    return None


def valid_context_window(value: Any) -> int | None:
    """Return a positive provider-supplied context capacity."""
    count = valid_token_count(value)
    return count if count is not None and count > 0 else None


def context_fraction(
    cache_read_tokens: int | None, context_window_tokens: int | None
) -> float | None:
    """Normalize the cache-read proxy by its observed model capacity."""
    if cache_read_tokens is None or context_window_tokens is None:
        return None
    return cache_read_tokens / context_window_tokens


def serialize_turn_token_entry(row: TurnTokenEntry) -> dict[str, object]:
    """Project one TurnTokenEntry into the sidecar schema with classified measures."""
    backend, provider_used = row["backend"], row["provider_used"]
    cache_read = classify_token_measure(
        backend, provider_used, "cache_read_tokens", row["cache_read_tokens"]
    )
    context_window = valid_context_window(row["context_window_tokens"])
    return {
        "backend": backend,
        "provider_used": provider_used,
        "message_id": row["message_id"],
        "request_id": row["request_id"],
        "timestamp": row["timestamp"],
        "model": row["model"],
        "input_tokens": classify_token_measure(
            backend, provider_used, "input_tokens", row["input_tokens"]
        ).to_dict(),
        "output_tokens": classify_token_measure(
            backend, provider_used, "output_tokens", row["output_tokens"]
        ).to_dict(),
        "cache_read_tokens": cache_read.to_dict(),
        "cache_write_tokens": classify_token_measure(
            backend, provider_used, "cache_write_tokens", row["cache_creation_tokens"]
        ).to_dict(),
        "peak_context": classify_token_measure(
            backend, provider_used, "peak_context", row["cache_read_tokens"]
        ).to_dict(),
        "context_window_tokens": context_window,
        "context_fraction": context_fraction(cache_read.value, context_window),
    }


def build_turn_token_entry(
    *,
    backend: str,
    provider_used: str | None = None,
    message_id: str | None = None,
    request_id: str | None = None,
    timestamp: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    context_window_tokens: int | None = None,
) -> TurnTokenEntry:
    """Build a complete row from already validated source evidence."""
    if not backend:
        raise ValueError("Turn token usage requires a non-empty backend")
    if provider_used is None:
        provider_used = {AGENT_BACKEND_CLAUDE_CODE: "anthropic"}.get(backend, backend)
    if not provider_used:
        raise ValueError("Turn token usage requires a non-empty provider_used")
    context_window_tokens = valid_context_window(context_window_tokens)
    return {
        "backend": backend,
        "provider_used": provider_used,
        "message_id": message_id,
        "request_id": request_id,
        "timestamp": timestamp,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "context_window_tokens": context_window_tokens,
        "context_fraction": context_fraction(cache_read_tokens, context_window_tokens),
    }


def merge_turn_usage(*groups: Iterable[TurnTokenEntry]) -> list[TurnTokenEntry]:
    """Merge snapshots by source pair and message ID, preserving first-seen order."""
    merged: list[TurnTokenEntry] = []
    positions: dict[tuple[str, str, str], int] = {}
    for group in groups:
        for row in group:
            message_id = row["message_id"]
            identity = (row["backend"], row["provider_used"], message_id or "")
            if not message_id or identity not in positions:
                copied: TurnTokenEntry = row.copy()
                merged.append(copied)
                if message_id:
                    positions[identity] = len(merged) - 1
                continue

            current = merged[positions[identity]]
            if current["request_id"] is None and row["request_id"] is not None:
                current["request_id"] = row["request_id"]
            if current["timestamp"] is None and row["timestamp"] is not None:
                current["timestamp"] = row["timestamp"]
            prior_model = current["model"]
            if row["model"] is not None:
                current["model"] = row["model"]
            field: Literal[
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_creation_tokens",
                "context_window_tokens",
            ]
            for field in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_creation_tokens",
                "context_window_tokens",
            ):
                if row[field] is not None:
                    current[field] = row[field]
            if (
                row["model"] is not None
                and row["model"] != prior_model
                and row["context_window_tokens"] is None
            ):
                current["context_window_tokens"] = None
            current["context_fraction"] = context_fraction(
                current["cache_read_tokens"], current["context_window_tokens"]
            )
    return merged
