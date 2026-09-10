"""Shared construction and snapshot merging for per-request token rows."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from autoskillit.core import TurnTokenEntry, fast_dumps, fsync_directory, get_logger

logger = get_logger(__name__)


def primary_model_identifier(token_usage: dict[str, Any] | None) -> str:
    """Return the aggregate bucket with the most output tokens."""
    if not token_usage:
        return ""
    model_breakdown = token_usage.get("model_breakdown", {})
    if not isinstance(model_breakdown, dict) or not model_breakdown:
        return ""
    return max(
        model_breakdown,
        key=lambda model: (
            model_breakdown[model].get("output_tokens", 0)
            if isinstance(model_breakdown[model], dict)
            else 0
        ),
    )


def resolve_session_label(step_name: str, dispatch_id: str) -> str:
    """Return the stable token/timing label for a completed session."""
    if step_name:
        return step_name
    if dispatch_id:
        return f"dispatch:{dispatch_id}"
    return "(ad-hoc)"


def is_parent_assistant_record(obj: dict[str, Any]) -> bool:
    """Return whether a record is a real parent assistant observation."""
    if obj.get("type") != "assistant" or obj.get("subagent_type"):
        return False
    message = obj.get("message")
    return not (isinstance(message, dict) and message.get("model") == "<synthetic>")


def first_parent_message_timestamps(text: str) -> dict[str, str]:
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
                output.write(fast_dumps(row, sort_keys=True) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
        fsync_directory(path.parent)
        return True
    except (OSError, TypeError, ValueError):
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


def build_turn_token_entry(
    *,
    backend: str,
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
    return {
        "backend": backend,
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
    """Merge later snapshots by non-empty message ID, preserving first-seen order."""
    merged: list[TurnTokenEntry] = []
    positions: dict[str, int] = {}
    for group in groups:
        for row in group:
            message_id = row["message_id"]
            if not message_id or message_id not in positions:
                copied: TurnTokenEntry = row.copy()
                merged.append(copied)
                if message_id:
                    positions[message_id] = len(merged) - 1
                continue

            current = merged[positions[message_id]]
            if current["request_id"] is None and row["request_id"] is not None:
                current["request_id"] = row["request_id"]
            if current["timestamp"] is None and row["timestamp"] is not None:
                current["timestamp"] = row["timestamp"]
            prior_model = current["model"]
            if row["model"] is not None:
                current["model"] = row["model"]
            if row["input_tokens"] is not None:
                current["input_tokens"] = row["input_tokens"]
            if row["output_tokens"] is not None:
                current["output_tokens"] = row["output_tokens"]
            if row["cache_read_tokens"] is not None:
                current["cache_read_tokens"] = row["cache_read_tokens"]
            if row["cache_creation_tokens"] is not None:
                current["cache_creation_tokens"] = row["cache_creation_tokens"]
            if row["context_window_tokens"] is not None:
                current["context_window_tokens"] = row["context_window_tokens"]
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
