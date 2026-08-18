"""Lossless, shape-preserving response budget enforcement for MCP handlers.

Re-exports the budget primitives, the spill envelope helpers, and the
``enforce_response_budget`` checkpoint machinery that MCP tools bind
through this package facade.
"""

from __future__ import annotations

# Module-level logger for tests that patch ``..._response_budget.logger``.
from autoskillit.core import atomic_write  # noqa: F401  (mock.patch reachability)
from autoskillit.core import get_logger as _get_logger
from autoskillit.server._recipe_segment_delivery import (  # noqa: F401  (mock.patch reachability)
    RecipeSegmentDeliveryError,
    build_post_effect_segment_failure,
)
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
    _emit_response_budget_event,
    emit_response_budget_failure,
)
from autoskillit.server._response_budget._projection import (
    _delivery_bound_summary,
    _project_json_object,
)
from autoskillit.server._response_budget._spill import (
    _artifact_path,
    bounded_response_budget_failure,
)

logger = _get_logger(__name__)

__all__ = [
    "RESPONSE_BUDGET_FAILURE_CAUSES",
    "RESPONSE_SPILL_METADATA_KEY",
    "RESPONSE_SPILL_METADATA_KEYS",
    "RESPONSE_SPILL_REASONS",
    "RESPONSE_SPILL_SCHEMA_DIGEST",
    "RESPONSE_SPILL_SCHEMA_VERSION",
    "_artifact_path",
    "_delivery_bound_summary",
    "_emit_response_budget_event",
    "_project_json_object",
    "RecipeSegmentDeliveryError",
    "atomic_write",
    "bounded_response_budget_failure",
    "build_post_effect_segment_failure",
    "emit_response_budget_failure",
    "enforce_response_budget",
    "logger",
    "post_effect_recipe_segment_failure",
    "shape_json_response",
]
