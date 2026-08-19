"""Failure-envelope builders for open_kitchen and recipe validation."""

from __future__ import annotations

import json
from typing import Any

from autoskillit.core import get_logger
from autoskillit.pipeline import ToolContext
from autoskillit.server.tools.tools_kitchen._open_kitchen_transition import (
    _transition_fields,
)

logger = get_logger(__name__)


def _kitchen_failure_envelope(
    exc: BaseException,
    stage: str,
    *,
    user_hint: str | None = None,
) -> str:
    """Return a JSON failure envelope for open_kitchen errors.

    Tool implementations catch exceptions locally and emit domain-specific
    envelopes with helpful ``user_visible_message`` values; the
    ``@track_response_size`` decorator only catches what slips through.
    """
    msg = user_hint or (
        f"open_kitchen failed during {stage}: {type(exc).__name__}. "
        f"Run 'autoskillit doctor' to diagnose, "
        f"or run 'autoskillit install' if the failure persists."
    )
    payload: dict[str, Any] = {
        "success": False,
        "kitchen": "failed",
        "user_visible_message": msg,
        "error": f"{type(exc).__name__}: {exc}",
        "stage": stage,
    }
    try:
        from autoskillit.server._state import _get_ctx_or_none  # circular-break

        tool_ctx: ToolContext | None = _get_ctx_or_none()
        if tool_ctx is not None:
            payload.update(_transition_fields(tool_ctx))
    except Exception:
        logger.warning("open_kitchen_transition_failure_envelope_failed", exc_info=True)
    return json.dumps(payload)


def _recipe_validation_error_response(name: str, result: dict[str, Any]) -> str:
    _structural_errs: list[str] = result.get("errors", [])
    if _structural_errs:
        _error_parts = _structural_errs[:3]
        if len(_structural_errs) > 3:
            _error_parts.append(f"+{len(_structural_errs) - 3} more errors")
    else:
        _all_errors = []
        for s in result.get("suggestions", []):
            if isinstance(s, dict) and s.get("severity") == "error":
                _line = f"[{s.get('rule', 'unknown-rule')}] {s.get('message', '')}"
                if s.get("origin"):
                    _line += f" (origin: {s['origin']})"
                if s.get("remedy"):
                    _line += f" — remedy: {s['remedy']}"
                _all_errors.append(_line)
        _error_parts = _all_errors[:3]
        if len(_all_errors) > 3:
            _error_parts.append(f"+{len(_all_errors) - 3} more errors")
    _error_detail = "; ".join(_error_parts) if _error_parts else "unknown structural error"
    _label = "structural validation" if _structural_errs else "validation"
    return json.dumps(
        {
            "success": False,
            "kitchen": "failed",
            "user_visible_message": (f"Recipe '{name}' failed {_label}: {_error_detail}"),
            "error": f"Recipe '{name}' failed validation: {_error_detail}",
            "stage": "recipe_validation",
            "errors": _structural_errs,
            "suggestions": result.get("suggestions", []),
        }
    )
