"""Pipeline tracker authority retain/release and auto-init for kitchen open."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from autoskillit.core import (
    ArtifactLease,
    KitchenProcessIdentity,
    TrackerAuthorityTarget,
    TrackerParticipantKey,
    get_logger,
    initialize_kitchen_tracker,
    pipeline_tracker_directory,
    register_active_kitchen,
    release_tracker_lease,
    retain_tracker_lease,
    try_retire_tracker,
    unregister_active_kitchen,
)
from autoskillit.pipeline import (
    KITCHEN_EFFECT_RECIPE_SERVING,
    ToolContext,
    get_kitchen_process_identity,
    transition_abort,
)
from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps
from autoskillit.server.tools.tools_kitchen._open_kitchen_errors import (
    _kitchen_failure_envelope,
)

logger = get_logger(__name__)


def _retain_kitchen_tracker_authority(
    tool_ctx: ToolContext,
) -> tuple[TrackerParticipantKey, ArtifactLease]:
    """Retain this process incarnation's kitchen tracker lease."""
    target = TrackerAuthorityTarget.for_project(
        tool_ctx.project_dir,
        tool_ctx.kitchen_id,
        expected=False,
    )
    with tool_ctx.tracker_leases_lock:
        identity = get_kitchen_process_identity(tool_ctx)
        key = TrackerParticipantKey(
            target=target,
            owner_kind="kitchen",
            owner_id=identity.kitchen_id,
            pid=identity.pid,
            create_time=identity.create_time,
            project_path=identity.project_path,
        )
        lease = retain_tracker_lease(tool_ctx.tracker_leases, key)
        tool_ctx.kitchen_tracker_key = key
    return key, lease


def _release_kitchen_tracker_authority(
    tool_ctx: ToolContext,
    *,
    unregister: bool,
    retire: bool,
) -> None:
    """Release exact ToolContext ownership and optionally retire its tracker."""
    with tool_ctx.tracker_leases_lock:
        key = tool_ctx.kitchen_tracker_key
        identity = tool_ctx.kitchen_process_identity
        if key is not None:
            release_tracker_lease(tool_ctx.tracker_leases, key)
        tool_ctx.kitchen_tracker_key = None
        if unregister:
            tool_ctx.kitchen_process_identity = None
    try:
        if unregister and identity is not None:
            unregister_active_kitchen(identity)
    finally:
        if retire and key is not None:
            try_retire_tracker(key.target)


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
    """Auto-derive and initialize the kitchen-scoped pipeline dependency tracker.

    Self-arming, server-internal counterpart to ``record_pipeline_step(op="init")``
    — runs at ``open_kitchen`` time from ``ctx.active_recipe_steps``, requiring
    no LLM action, mirroring how ingredient locks are primed. The core authority
    seam performs the locked merge while this caller retains the kitchen lease.

    Idempotent across the deferred-override re-call pattern: an existing
    tracker's step statuses and previously-tracked dependency keys are
    preserved rather than overwritten.
    """
    active_steps = tool_ctx.active_recipe_steps
    if not active_steps:
        return None
    try:
        deps = _derive_phase_a_deps(active_steps)
    except Exception:
        logger.warning("pipeline_tracker_auto_init_deps_failed", exc_info=True)
        return None
    if not deps:
        return None

    key, lease = _retain_kitchen_tracker_authority(tool_ctx)
    steps: dict[str, dict[str, str]] = {name: {"status": "pending"} for name in active_steps}
    dependencies: dict[str, list[str]] = dict(deps)

    tracker_data = {
        "kitchen_id": tool_ctx.kitchen_id,
        "pipeline_id": tool_ctx.kitchen_id,
        "steps": steps,
        "dependencies": dependencies,
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


def _register_active_recipe_kitchen(ctx: ToolContext) -> None:
    """Publish one kitchen to both process and recipe-generation lifecycles."""
    from autoskillit.server._recipe_generation import activate_kitchen  # circular-break

    identity = cast(KitchenProcessIdentity, ctx.kitchen_process_identity)
    register_active_kitchen(identity)
    activate_kitchen(identity.kitchen_id)
