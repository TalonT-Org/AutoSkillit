"""MCP tool: record_pipeline_step — pipeline step tracker init, status, and complete."""

from __future__ import annotations

# Re-exports for tests that patch symbols via the package facade.
from autoskillit.server.recipe._recipe_segment_delivery import (
    prepare_recipe_segment_delivery,  # noqa: F401
)

# Side-effect imports: register @mcp.tool() decorators on the FastMCP server.
from autoskillit.server.tools.tools_pipeline_tracker import (  # noqa: F401
    _handlers,
    _status,
)

# Public MCP tool re-exports.
from autoskillit.server.tools.tools_pipeline_tracker._handlers import (
    _handle_complete,
    _handle_init,
    _handle_status,
    complete_run_skill_result,
    mark_step_complete,
    mark_step_skipped,
    record_pipeline_step,
    recover_run_skill_result,
)
from autoskillit.server.tools.tools_pipeline_tracker._status import (
    _build_tracker_steps,
    _compute_status_counts,
)

__all__ = [
    "complete_run_skill_result",
    "mark_step_complete",
    "mark_step_skipped",
    "recover_run_skill_result",
    "record_pipeline_step",
    "_build_tracker_steps",
    "_compute_status_counts",
    "_handle_complete",
    "_handle_init",
    "_handle_status",
]
