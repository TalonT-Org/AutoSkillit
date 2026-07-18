"""Lossless, shape-preserving enforcement for MCP handler responses."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    atomic_write,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.config import OutputBudgetConfig

logger = get_logger(__name__)

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
RESPONSE_SPILL_REASONS = frozenset({"oversized_values", "minimal_projection", "plain_text"})
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


def _serialized(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _canonical_json(value)


def _bounded_tool_name(tool_name: str) -> str:
    return tool_name.encode("ascii", "replace").decode("ascii")[:64]


def _emit_response_budget_event(event: str, **payload: Any) -> None:
    with suppress(Exception):
        logger.info(event, **payload)


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


def _project_value(value: Any, limit: int) -> tuple[Any, int, int]:
    """Return a bounded same-category value and aggregate omission counts."""
    if isinstance(value, str):
        projected, omitted = _preview_string(value, limit)
        return projected, omitted, 0
    if value is None or isinstance(value, (bool, int, float)):
        return value, 0, 0
    if isinstance(value, list):
        if not value:
            return [], 0, 0
        item_limit = max(16, limit // min(len(value), 4))
        candidates = value if len(value) <= 4 else [value[0], value[1], value[-2], value[-1]]
        projected_items: list[Any] = []
        omitted_chars = 0
        omitted_items = max(0, len(value) - len(candidates))
        for item in candidates:
            projected, chars, items = _project_value(item, item_limit)
            projected_items.append(projected)
            omitted_chars += chars
            omitted_items += items
        return projected_items, omitted_chars, omitted_items
    if isinstance(value, dict):
        if not value:
            return {}, 0, 0
        item_limit = max(16, limit // max(1, min(len(value), 8)))
        projected_dict: dict[str, Any] = {}
        omitted_chars = 0
        omitted_items = 0
        for key, item in value.items():
            projected, chars, items = _project_value(item, item_limit)
            projected_dict[str(key)] = projected
            omitted_chars += chars
            omitted_items += items
        return projected_dict, omitted_chars, omitted_items
    text, omitted = _preview_string(str(value), limit)
    return text, omitted, 0


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


def _project_json_object(
    parsed: dict[str, Any],
    *,
    metadata: dict[str, Any],
    max_bytes: int,
    inline_chars: int,
) -> str | None:
    if RESPONSE_SPILL_METADATA_KEY in parsed:
        return None

    key_count = max(1, len(parsed))
    value_limit = max(16, min(inline_chars, max_bytes // (key_count + 1)))
    while value_limit >= 16:
        projected: dict[str, Any] = {}
        omitted_chars = 0
        omitted_items = 0
        for key, value in parsed.items():
            item, chars, items = _project_value(value, value_limit)
            projected[key] = item
            omitted_chars += chars
            omitted_items += items
        projected[RESPONSE_SPILL_METADATA_KEY] = {
            **_spill_metadata(
                metadata,
                reason="oversized_values",
                omitted_chars=omitted_chars,
                omitted_items=omitted_items,
            )
        }
        rendered = _finalize_envelope(projected, max_bytes=max_bytes)
        if len(rendered.encode("utf-8")) <= max_bytes:
            return rendered
        value_limit //= 2

    minimal = {key: _minimal_same_type(value) for key, value in parsed.items()}
    omitted_chars = 0
    omitted_items = 0
    for value in parsed.values():
        chars, items = _total_omissions(value)
        omitted_chars += chars
        omitted_items += items
    minimal[RESPONSE_SPILL_METADATA_KEY] = _spill_metadata(
        metadata,
        reason="minimal_projection",
        omitted_chars=omitted_chars,
        omitted_items=omitted_items,
    )
    rendered = _finalize_envelope(minimal, max_bytes=max_bytes)
    return rendered if len(rendered.encode("utf-8")) <= max_bytes else None


def _plain_spill_envelope(
    original: str,
    *,
    metadata: dict[str, Any],
    max_bytes: int,
    inline_chars: int,
) -> str | None:
    preview_limit = max(0, min(inline_chars, max_bytes // 3))
    while True:
        preview, omitted_chars = _preview_string(original, preview_limit)
        envelope: dict[str, Any] = {
            RESPONSE_SPILL_METADATA_KEY: _spill_metadata(
                metadata,
                reason="plain_text",
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


def enforce_response_budget(
    result: Any,
    *,
    tool_name: str,
    artifact_dir: Path | None,
    config: OutputBudgetConfig,
    force_spill: bool = False,
) -> Any:
    """Return a bounded response of the same handler type.

    Oversized content is atomically persisted before a projection is returned.
    Artifact failure and missing-context cases fail closed without echoing the
    original payload.
    """
    try:
        original = _serialized(result)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return bounded_response_budget_failure(
            result,
            cause="serialization_failed",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=0,
        )
    original_bytes = original.encode("utf-8")
    original_size = len(original_bytes)
    exemption = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.get(tool_name)
    if exemption is not None:
        if len(original) <= exemption.max_chars and original_size <= exemption.max_utf8_bytes:
            _emit_response_budget_event(
                "response_budget_exemption",
                tool_name=_bounded_tool_name(tool_name),
                measurement_id=exemption.measurement_id,
                original_chars=len(original),
                original_utf8_bytes=original_size,
                max_chars=exemption.max_chars,
                max_utf8_bytes=exemption.max_utf8_bytes,
            )
            return result
        return bounded_response_budget_failure(
            result,
            cause="exemption_ceiling_exceeded",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
        )
    if not force_spill and len(original_bytes) <= config.response_max_bytes:
        return result
    if artifact_dir is None:
        return bounded_response_budget_failure(
            result,
            cause="context_unavailable",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
        )

    path = _artifact_path(artifact_dir, tool_name)
    try:
        atomic_write(path, original)
    except OSError:
        return bounded_response_budget_failure(
            result,
            cause="artifact_publication_failed",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
        )

    published = str(path.resolve())
    metadata = {
        "artifact_path": published,
        "sha256": hashlib.sha256(original_bytes).hexdigest(),
        "original_utf8_bytes": len(original_bytes),
    }

    parsed: Any = None
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, ValueError, RecursionError):
            parsed = None
    else:
        parsed = result

    rendered: str | None = None
    try:
        if isinstance(parsed, dict):
            rendered = _project_json_object(
                parsed,
                metadata=metadata,
                max_bytes=config.response_max_bytes,
                inline_chars=config.inline_max_chars,
            )
        if rendered is None and not isinstance(parsed, dict):
            rendered = _plain_spill_envelope(
                original,
                metadata=metadata,
                max_bytes=config.response_max_bytes,
                inline_chars=config.inline_max_chars,
            )
    except _ProjectionNonconvergentError as exc:
        _emit_response_budget_event(
            "response_budget_projection_nonconvergent",
            tool_name=_bounded_tool_name(tool_name),
            detail=str(exc),
        )
        rendered = bounded_response_budget_failure(
            "",
            cause="projection_nonconvergent",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
            artifact_path=published,
        )
    except RecursionError:
        # Projection recurses per nesting level; the artifact is already
        # persisted, so the recovery pointer must survive the stack failure.
        rendered = bounded_response_budget_failure(
            "",
            cause="irreducible_shape",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
            artifact_path=published,
        )
    if rendered is None:
        rendered = bounded_response_budget_failure(
            "",
            cause="irreducible_shape",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
            artifact_path=published,
        )

    _emit_response_budget_event(
        "response_budget_spill",
        tool_name=_bounded_tool_name(tool_name),
        original_utf8_bytes=original_size,
        projected_utf8_bytes=len(rendered.encode("utf-8")),
    )
    if isinstance(result, str):
        return rendered
    try:
        return json.loads(rendered)
    except (ValueError, RecursionError):
        return {"success": False, "error": "response_budget_projection_invalid"}


def shape_json_response(
    payload: dict[str, Any],
    *,
    tool_name: str,
    artifact_dir: Path,
    config: OutputBudgetConfig,
) -> str:
    """Serialize a tool dict, spilling once it crosses the source threshold."""
    rendered = json.dumps(payload)
    if len(rendered) <= config.inline_max_chars:
        return rendered
    shaped = enforce_response_budget(
        rendered,
        tool_name=tool_name,
        artifact_dir=artifact_dir,
        config=config,
        force_spill=True,
    )
    return shaped if isinstance(shaped, str) else json.dumps(shaped)


__all__ = [
    "RESPONSE_BUDGET_FAILURE_CAUSES",
    "RESPONSE_SPILL_METADATA_KEY",
    "RESPONSE_SPILL_METADATA_KEYS",
    "RESPONSE_SPILL_REASONS",
    "RESPONSE_SPILL_SCHEMA_DIGEST",
    "RESPONSE_SPILL_SCHEMA_VERSION",
    "bounded_response_budget_failure",
    "emit_response_budget_failure",
    "enforce_response_budget",
    "shape_json_response",
]
