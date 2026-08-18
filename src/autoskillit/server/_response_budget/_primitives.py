"""Lossless, shape-preserving enforcement for MCP handler responses."""

import hashlib
import json
from contextlib import suppress
from typing import Any

from autoskillit.core import ASCII_YAML_POLICY, Utf8ByteLimit

# Late-binding for monkeypatch reach: tests patch
# "autoskillit.server._response_budget.logger" (the package facade), so the
# logger must be resolved via attribute access on the package at call time
# rather than a separate logger instance owned by this submodule.
from autoskillit.server import _response_budget as _response_budget_pkg

RESPONSE_SPILL_METADATA_KEY = "_autoskillit_response_spill"
RESPONSE_SPILL_SCHEMA_VERSION = 1
RESPONSE_SPILL_METADATA_KEYS = frozenset(
    {
        "schema_version",
        "artifact_path",
        "sha256",
        "original_utf8_bytes",
        "projected_utf8_bytes",
        "omitted_chars",
        "omitted_items",
        "reason",
    }
)
RESPONSE_SPILL_REASONS = frozenset(
    {"oversized_values", "minimal_projection", "plain_text", "delivery_bound"}
)
RESPONSE_SPILL_SCHEMA_DIGEST = hashlib.sha256(
    json.dumps(
        {
            "metadata_key": RESPONSE_SPILL_METADATA_KEY,
            "metadata_keys": sorted(RESPONSE_SPILL_METADATA_KEYS),
            "reasons": sorted(RESPONSE_SPILL_REASONS),
            "schema_version": RESPONSE_SPILL_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()

RESPONSE_BUDGET_FAILURE_CAUSES = frozenset(
    {
        "context_unavailable",
        "artifact_publication_failed",
        "serialization_failed",
        "projection_nonconvergent",
        "irreducible_shape",
        "schema_nonconforming",
        "exemption_ceiling_exceeded",
        "internal_invariant_failed",
    }
)


class _ProjectionNonconvergentError(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _estimated_tokens(original_size: int) -> int:
    """Estimate tokens through the explicit ASCII-YAML conversion policy.

    Uses the general output token limit as a coarse
    transport-layer estimate, not a tokenizer count. Used to compare
    payload size against ``selected_result_token_limit``.
    """
    if original_size == 0:
        return 0
    return ASCII_YAML_POLICY.to_tokens(Utf8ByteLimit(original_size)).value


def _serialized(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _canonical_json(value)


def _bounded_tool_name(tool_name: str) -> str:
    return tool_name.encode("ascii", "replace").decode("ascii")[:64]


def _emit_response_budget_event(event: str, **payload: Any) -> None:
    with suppress(Exception):
        _response_budget_pkg.logger.info(event, **payload)


def emit_response_budget_failure(tool_name: str, cause: str, original_utf8_bytes: int) -> None:
    if cause not in RESPONSE_BUDGET_FAILURE_CAUSES:
        cause = "internal_invariant_failed"
    _emit_response_budget_event(
        "response_budget_failure",
        tool_name=_bounded_tool_name(tool_name),
        cause=cause,
        original_utf8_bytes=original_utf8_bytes,
    )


def _preview_string(value: str, limit: int) -> tuple[str, int]:
    if len(value) <= limit:
        return value, 0
    if limit <= 16:
        return value[:limit], len(value) - limit
    marker = "…[omitted]…"
    available = max(0, limit - len(marker))
    head = available // 2
    tail = available - head
    return value[:head] + marker + (value[-tail:] if tail else ""), len(value) - available


def _minimal_same_type(value: Any) -> Any:
    if isinstance(value, str):
        return ""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return ""
