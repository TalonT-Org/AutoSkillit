"""Decode raw token observations and assemble the session-evidence sidecar record."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autoskillit.core import (
    TURN_USAGE_SCHEMA_VERSION,
    SerializedTokenMeasure,
    TokenMeasure,
    get_logger,
)

logger = get_logger(__name__)


def serialized_token_measure(raw: object) -> SerializedTokenMeasure:
    """Preserve a structured measure or classify an in-process numeric observation."""
    return TokenMeasure.measure_from_raw_to_serialized(raw)


def build_token_usage_record(
    token_usage: Mapping[str, Any] | None,
    *,
    label: str,
    backend: str,
    timing_seconds: float | None,
    order_id: str,
    loc_insertions: int,
    loc_deletions: int,
    provider_used: str,
    model_identifier: str,
    configured_model: str,
    profile_name: str,
    dispatch_id: str,
    campaign_id: str,
    sidecar_published: bool,
    turn_usage_count: int,
) -> dict[str, Any]:
    """Build the versioned token sidecar without flattening measure state."""
    usage = token_usage or {}
    return {
        "session_label": label,
        "backend": backend,
        "input_tokens": serialized_token_measure(usage.get("input_tokens")),
        "output_tokens": serialized_token_measure(usage.get("output_tokens")),
        "cache_write_tokens": serialized_token_measure(
            usage.get("cache_write_tokens", usage.get("cache_creation_input_tokens"))
        ),
        "cache_read_tokens": serialized_token_measure(
            usage.get("cache_read_tokens", usage.get("cache_read_input_tokens"))
        ),
        "timing_seconds": timing_seconds or 0.0,
        "order_id": order_id,
        "loc_insertions": loc_insertions,
        "loc_deletions": loc_deletions,
        "peak_context": serialized_token_measure(usage.get("peak_context")),
        "turn_count": usage.get("turn_count", 0),
        "provider_used": str(usage.get("provider_used") or provider_used or backend),
        "model_identifier": model_identifier,
        "configured_model": configured_model,
        "profile_name": profile_name,
        "dispatch_id": dispatch_id,
        "campaign_id": campaign_id,
        "turn_usage_file": "turn_usage.jsonl" if sidecar_published else None,
        "turn_usage_count": turn_usage_count if sidecar_published else 0,
        "turn_usage_schema_version": TURN_USAGE_SCHEMA_VERSION,
    }
