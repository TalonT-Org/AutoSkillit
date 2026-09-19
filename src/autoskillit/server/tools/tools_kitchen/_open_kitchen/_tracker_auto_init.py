"""Open-kitchen pipeline-tracker auto-init lifecycle.

Cohorts the three helpers that initialize, sweep, and abort on a kitchen's
pipeline tracker before the first recipe projection is served.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import (
    TrackerAuthorityTarget,
    get_logger,
    initialize_kitchen_tracker,
    pipeline_tracker_directory,
)
from autoskillit.pipeline import (
    KITCHEN_EFFECT_RECIPE_SERVING,
    ToolContext,
    transition_abort,
)
from autoskillit.server._tracker_authority import (
    _release_kitchen_tracker_authority,
    _retain_kitchen_tracker_authority,
    try_retire_tracker,
)
from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps
from autoskillit.server.tools.tools_kitchen._open_kitchen_errors import (
    _kitchen_failure_envelope,
)

logger = get_logger(__name__)


def prune_stale_kitchen_state(project_dir: Path, current_kitchen_id: str) -> None:
    """Offer each foreign tracker to the core retirement authority."""
    tracker_dir = pipeline_tracker_directory(project_dir)
    if not tracker_dir.is_dir():
        return

    for tracker_file in tracker_dir.glob("*.json"):
        if tracker_file.name.startswith(".") or tracker_file.stem == current_kitchen_id:
            continue
        try:
            target = TrackerAuthorityTarget.for_project(
                project_dir,
                tracker_file.stem,
                expected=False,
            )
        except ValueError as exc:
            logger.warning(
                "invalid_stale_tracker_candidate",
                path=str(tracker_file),
                error=str(exc),
            )
            continue
        try_retire_tracker(target)


def _auto_init_pipeline_tracker(tool_ctx: ToolContext) -> str | None:
    """Initialize the kitchen tracker from the finalized recipe projection."""
    active_steps = tool_ctx.active_recipe_steps
    projection = tool_ctx.active_recipe_projection
    if not active_steps or projection is None:
        return None
    try:
        deps = _derive_phase_a_deps(projection)
    except Exception:
        logger.warning("pipeline_tracker_auto_init_deps_failed", exc_info=True)
        return None
    if not deps:
        if not tool_ctx.kitchen_id:
            return None
        existing_target = TrackerAuthorityTarget.for_project(
            tool_ctx.project_dir,
            tool_ctx.kitchen_id,
            expected=False,
        )
        if not (existing_target.path.exists() or existing_target.path.is_symlink()):
            return None

    key, lease = _retain_kitchen_tracker_authority(tool_ctx)
    steps: dict[str, dict[str, str]] = {name: {"status": "pending"} for name in active_steps}
    tracker_data = {
        "kitchen_id": tool_ctx.kitchen_id,
        "pipeline_id": tool_ctx.kitchen_id,
        "steps": steps,
        "dependencies": dict(deps),
        "initialized_at": datetime.now(UTC).isoformat(),
    }
    try:
        result = initialize_kitchen_tracker(key.target, lease, tracker_data)
    except Exception:
        _release_kitchen_tracker_authority(tool_ctx, unregister=False, retire=False)
        raise
    if result.error is not None:
        _release_kitchen_tracker_authority(tool_ctx, unregister=False, retire=False)
    return result.error


def _pipeline_tracker_auto_init_failure(tool_ctx: ToolContext, error: str) -> str:
    """Abort kitchen opening after tracker initialization fails."""
    transition_abort(tool_ctx, KITCHEN_EFFECT_RECIPE_SERVING)
    tool_ctx.gate.disable()
    tool_ctx.gate_infrastructure_ready = False
    return _kitchen_failure_envelope(
        RuntimeError(error),
        stage="pipeline_tracker_auto_init",
        user_hint=error,
    )
