"""MCP tool: record_pipeline_step — pipeline step tracker init, status, and complete."""

from __future__ import annotations

# Re-exports for tests that patch symbols via the package facade.
from autoskillit.server._recipe_segment_delivery import prepare_recipe_segment_delivery

# Side-effect imports: register @mcp.tool() decorators on the FastMCP server.
from autoskillit.server.tools.tools_pipeline_tracker import (  # noqa: F401
    _authority,
    _handlers,
    _status,
)

# Internal helpers re-exported for tools_execution.py and other sibling callers.
from autoskillit.server.tools.tools_pipeline_tracker._authority import (
    _authority_blocks_dependency_check,
    _release_context_tracker,
    _resolve_skipped_steps,
    _restore_reserved_tracker_authority,
    _retain_context_tracker,
    _select_tracker_authority,
    read_tracker_identity,
    select_tracker_target,
)

# Public MCP tool re-exports.
from autoskillit.server.tools.tools_pipeline_tracker._handlers import (
    _handle_complete,
    _handle_init,
    _handle_status,
    complete_run_skill_result,
    mark_step_complete,
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
    "recover_run_skill_result",
    "record_pipeline_step",
    "_authority_blocks_dependency_check",
    "_build_tracker_steps",
    "_compute_status_counts",
    "_handle_complete",
    "_handle_init",
    "_handle_status",
    "_release_context_tracker",
    "_resolve_skipped_steps",
    "_retain_context_tracker",
    "_restore_reserved_tracker_authority",
    "_select_tracker_authority",
    "read_tracker_identity",
    "select_tracker_target",
]
