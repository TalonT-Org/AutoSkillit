"""Lossless, shape-preserving enforcement for MCP handler responses."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import atomic_write, get_logger

if TYPE_CHECKING:
    from autoskillit.config import OutputBudgetConfig

logger = get_logger(__name__)

RESPONSE_SPILL_METADATA_KEY = "_autoskillit_response_spill"
RESPONSE_BACKSTOP_EXEMPT_TOOLS = frozenset({"open_kitchen", "load_recipe"})


def _serialized(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


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
    rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(rendered.encode("utf-8")) <= max_bytes:
        return rendered
    return '{"success":false,"error":"response_budget_failure"}'


def _artifact_path(artifact_dir: Path, tool_name: str) -> Path:
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in tool_name)
    return artifact_dir / f"{safe_name or 'response'}_{uuid.uuid4().hex[:8]}.log"


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
            **metadata,
            "omitted_chars": omitted_chars,
            "omitted_items": omitted_items,
            "reason": "oversized_values",
        }
        rendered = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
        projected[RESPONSE_SPILL_METADATA_KEY]["projected_utf8_bytes"] = len(
            rendered.encode("utf-8")
        )
        rendered = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
        if len(rendered.encode("utf-8")) <= max_bytes:
            return rendered
        value_limit //= 2

    minimal = {key: _minimal_same_type(value) for key, value in parsed.items()}
    minimal[RESPONSE_SPILL_METADATA_KEY] = {
        **metadata,
        "omitted_chars": 0,
        "omitted_items": 0,
        "reason": "minimal_projection",
    }
    rendered = json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))
    minimal[RESPONSE_SPILL_METADATA_KEY]["projected_utf8_bytes"] = len(rendered.encode("utf-8"))
    rendered = json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))
    return rendered if len(rendered.encode("utf-8")) <= max_bytes else None


def _plain_spill_envelope(
    original: str,
    *,
    metadata: dict[str, Any],
    max_bytes: int,
    inline_chars: int,
) -> str:
    preview, _ = _preview_string(original, min(inline_chars, max_bytes // 3))
    envelope: dict[str, Any] = {
        RESPONSE_SPILL_METADATA_KEY: {
            **metadata,
            "projected_utf8_bytes": 0,
            "reason": "plain_text",
        },
        "preview": preview,
    }
    rendered = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    envelope[RESPONSE_SPILL_METADATA_KEY]["projected_utf8_bytes"] = len(rendered.encode("utf-8"))
    rendered = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    while len(rendered.encode("utf-8")) > max_bytes and preview:
        preview = preview[: len(preview) // 2]
        envelope["preview"] = preview
        rendered = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return rendered


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
    if tool_name in RESPONSE_BACKSTOP_EXEMPT_TOOLS:
        return result

    original = _serialized(result)
    original_bytes = original.encode("utf-8")
    if not force_spill and len(original_bytes) <= config.response_max_bytes:
        return result
    if artifact_dir is None:
        failure = _bounded_failure(
            reason="response_budget_context_unavailable",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
        )
        return failure if isinstance(result, str) else {"success": False, "error": failure}

    path = _artifact_path(artifact_dir, tool_name)
    try:
        atomic_write(path, original)
    except Exception:
        logger.error("response_budget_artifact_write_failed", tool_name=tool_name, exc_info=True)
        failure = _bounded_failure(
            reason="response_budget_artifact_write_failed",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
        )
        return failure if isinstance(result, str) else {"success": False, "error": failure}

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
        except (TypeError, ValueError):
            parsed = None
    else:
        parsed = result

    rendered: str | None = None
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
    if rendered is None:
        rendered = _bounded_failure(
            reason="response_budget_irreducible_shape",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            artifact_path=published,
        )

    logger.info(
        "response_budget_spill",
        tool_name=tool_name,
        original_utf8_bytes=len(original_bytes),
        artifact_utf8_bytes=len(original_bytes),
    )
    if isinstance(result, str):
        return rendered
    try:
        return json.loads(rendered)
    except ValueError:
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
    "RESPONSE_BACKSTOP_EXEMPT_TOOLS",
    "RESPONSE_SPILL_METADATA_KEY",
    "enforce_response_budget",
    "shape_json_response",
]
