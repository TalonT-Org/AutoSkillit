"""Lossless, shape-preserving response budget enforcement for MCP handlers.

Re-exports the budget primitives, the spill envelope helpers, and the
``enforce_response_budget`` checkpoint machinery that MCP tools bind
through this package facade.
"""

from __future__ import annotations

from autoskillit.server._response_budget._enforce import (
    enforce_response_budget,
    post_effect_recipe_segment_failure,
    shape_json_response,
)
from autoskillit.server._response_budget._primitives import (
    RESPONSE_BUDGET_FAILURE_CAUSES,
    RESPONSE_SPILL_METADATA_KEY,
    RESPONSE_SPILL_METADATA_KEYS,
    RESPONSE_SPILL_REASONS,
    RESPONSE_SPILL_SCHEMA_DIGEST,
    RESPONSE_SPILL_SCHEMA_VERSION,
    emit_response_budget_failure,
)
from autoskillit.server._response_budget._spill import bounded_response_budget_failure

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
    "post_effect_recipe_segment_failure",
    "shape_json_response",
]
