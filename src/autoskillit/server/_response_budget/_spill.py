"""Bounded-failure envelope + artifact-path machinery for response budget spillover."""

import json
import uuid
from pathlib import Path
from typing import Any

from autoskillit.server._response_budget._primitives import (
    RESPONSE_SPILL_METADATA_KEY,
    RESPONSE_SPILL_SCHEMA_VERSION,
    _bounded_tool_name,
    _canonical_json,
    _preview_string,
    _ProjectionNonconvergentError,
    emit_response_budget_failure,
)


def _bounded_failure(
    *,
    reason: str,
    tool_name: str,
    max_bytes: int,
    artifact_path: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "success": False,
        "error": reason,
        "tool_name": tool_name,
    }
    if artifact_path is not None:
        payload["artifact_path"] = artifact_path
    candidates = (
        _canonical_json(payload),
        '{"success":false,"error":"response_budget_failure"}',
        '{"success":false}',
        "{}",
        "",
    )
    return next(
        candidate for candidate in candidates if len(candidate.encode("utf-8")) <= max_bytes
    )


def bounded_response_budget_failure(
    result: Any,
    *,
    cause: str,
    tool_name: str,
    max_bytes: int,
    original_utf8_bytes: int,
    artifact_path: str | None = None,
) -> Any:
    emit_response_budget_failure(tool_name, cause, original_utf8_bytes)
    rendered = _bounded_failure(
        reason=f"response_budget_{cause}",
        tool_name=_bounded_tool_name(tool_name),
        max_bytes=max_bytes,
        artifact_path=artifact_path,
    )
    if isinstance(result, str):
        return rendered
    try:
        return json.loads(rendered)
    except ValueError:
        return {"success": False, "error": "response_budget_failure"}


def _artifact_path(artifact_dir: Path, tool_name: str) -> Path:
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in tool_name)
    return artifact_dir / f"{safe_name or 'response'}_{uuid.uuid4().hex[:8]}.log"


def _finalize_envelope(envelope: dict[str, Any], *, max_bytes: int) -> str:
    metadata = envelope.get(RESPONSE_SPILL_METADATA_KEY)
    if not isinstance(metadata, dict) or "projected_utf8_bytes" not in metadata:
        raise _ProjectionNonconvergentError("spill metadata missing projected byte field")
    max_decimal_width = len(str(max(0, max_bytes)))
    seen: set[tuple[int, int]] = set()
    for _ in range(max_decimal_width + 2):
        rendered = _canonical_json(envelope)
        measured = len(rendered.encode("utf-8"))
        current = metadata.get("projected_utf8_bytes")
        if current == measured:
            return rendered
        state = (current if isinstance(current, int) else -1, measured)
        if state in seen:
            break
        seen.add(state)
        metadata["projected_utf8_bytes"] = measured
    raise _ProjectionNonconvergentError(
        "projected byte fixed point did not converge: "
        f"measured={len(_canonical_json(envelope).encode('utf-8'))} "
        f"projected={metadata.get('projected_utf8_bytes')!r} "
        f"max_bytes={max_bytes} attempted_states={len(seen)}"
    )


def _spill_metadata(
    metadata: dict[str, Any],
    *,
    reason: str,
    omitted_chars: int,
    omitted_items: int,
) -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_SPILL_SCHEMA_VERSION,
        **metadata,
        "projected_utf8_bytes": 0,
        "omitted_chars": omitted_chars,
        "omitted_items": omitted_items,
        "reason": reason,
    }


def _total_omissions(value: Any) -> tuple[int, int]:
    if isinstance(value, str):
        return len(value), 0
    if isinstance(value, list):
        nested = [_total_omissions(item) for item in value]
        return sum(chars for chars, _ in nested), len(value) + sum(items for _, items in nested)
    if isinstance(value, dict):
        nested = [_total_omissions(item) for item in value.values()]
        return sum(chars for chars, _ in nested), len(value) + sum(items for _, items in nested)
    return 0, 0


def _plain_spill_envelope(
    original: str,
    *,
    metadata: dict[str, Any],
    max_bytes: int,
    inline_chars: int,
    reason: str = "plain_text",
) -> str | None:
    preview_limit = max(0, min(inline_chars, max_bytes // 3))
    while True:
        preview, omitted_chars = _preview_string(original, preview_limit)
        envelope: dict[str, Any] = {
            RESPONSE_SPILL_METADATA_KEY: _spill_metadata(
                metadata,
                reason=reason,
                omitted_chars=omitted_chars,
                omitted_items=0,
            ),
            "preview": preview,
        }
        rendered = _finalize_envelope(envelope, max_bytes=max_bytes)
        if len(rendered.encode("utf-8")) <= max_bytes:
            return rendered
        if preview_limit == 0:
            return None
        preview_limit //= 2


def _serialized_string_prefix_length(value: str, max_bytes: int) -> int:
    """Return the largest prefix whose serialized string body fits ``max_bytes``."""
    low, high = 0, len(value)
    best = 0
    while low <= high:
        midpoint = (low + high) // 2
        serialized_bytes = len(_canonical_json(value[:midpoint]).encode("utf-8")) - 2
        if serialized_bytes <= max_bytes:
            best = midpoint
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best
