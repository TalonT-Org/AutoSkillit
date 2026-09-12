"""Backward-compat shim for ``recipe/_api_orchestration_text.py``.

Real implementation: ``autoskillit.recipe.api_orchestration._api_orchestration_text`` (#4951).
Preserves old import path ``autoskillit.recipe._api_orchestration_text``.
"""

from __future__ import annotations

from autoskillit.recipe.api_orchestration._api_orchestration_text import (  # noqa: F401
    ROUTING_AUTHORITY_CLAUSE,
    STEP_SKIP_SEMANTICS_CLAUSE,
    Recipe,
    _build_orchestration_rules,
    _build_stop_step_semantics,
    _infer_stop_failure,
    annotations,
    build_parameter_forwarding_rules,
    extract_sentinel_json_blocks,
    get_logger,
    json,
    logger,
)

__all__ = [
    "_build_orchestration_rules",
    "_build_stop_step_semantics",
    "_infer_stop_failure",
]
