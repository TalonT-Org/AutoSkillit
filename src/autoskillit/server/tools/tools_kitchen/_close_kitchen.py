"""close_kitchen tool handler and teardown internals."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    atomic_write,
    fast_dumps,
    get_logger,
    release_tracker_lease,
    try_retire_tracker,
)
from autoskillit.fleet import FleetSemaphore
from autoskillit.pipeline import closed_kitchen_open_state
from autoskillit.server import mcp
from autoskillit.server._guards import _require_orchestrator_exact
from autoskillit.server._misc import _hook_config_path, resolve_log_dir
from autoskillit.server._notify import track_response_size
from autoskillit.server._recipe_delivery import retire_recipe_artifacts
from autoskillit.server._recipe_execution import clear_recipe_execution
from autoskillit.server._recipe_generation import (
    retire_kitchen as retire_recipe_generation,
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._overlay_state import locked_overlay
from autoskillit.server.tools.tools_kitchen._tracker_authority import (
    _release_kitchen_tracker_authority,
)

logger = get_logger(__name__)


def _close_kitchen_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    authority = ctx.run_skill_completion
    if authority is not None and not authority.clear_if_idle():
        raise RuntimeError("run_skill completion is still active")
    if ctx.quota_refresh_task is not None:
        ctx.quota_refresh_task.cancel()
        ctx.quota_refresh_task = None
    baseline_config = deepcopy(ctx._baseline_config)
    baseline_lock = FleetSemaphore(
        max_concurrent=baseline_config.fleet.max_concurrent_dispatches,
        timeout=baseline_config.fleet.acquire_timeout_sec,
    )
    hook_cfg_path = _hook_config_path(ctx.project_dir)
    with locked_overlay(ctx.project_dir) as (overlay_path, _):
        ctx.gate.disable()
        try:
            hook_cfg_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("hook_config_remove_failed", path=str(hook_cfg_path))
        try:
            overlay_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("hook_config_overlay_remove_failed", path=str(overlay_path))
        ctx._session_config_overrides.clear()
        ctx.config = baseline_config
        ctx.fleet_lock = baseline_lock
    try:
        _release_kitchen_tracker_authority(ctx, unregister=True, retire=True)
    except Exception:
        logger.warning("close_kitchen_tracker_authority_release_failed", exc_info=True)
    with ctx.tracker_leases_lock:
        abandoned_targets = {key.target for key in ctx.tracker_leases}
        for key in list(ctx.tracker_leases):
            release_tracker_lease(ctx.tracker_leases, key)
    for target in abandoned_targets:
        try_retire_tracker(target)
    if isinstance(ctx.kitchen_id, str) and ctx.kitchen_id:
        if isinstance(ctx.temp_dir, Path) and not retire_recipe_artifacts(
            ctx.temp_dir,
            kitchen_id=ctx.kitchen_id,
        ):
            logger.warning("close_kitchen_recipe_artifact_retirement_failed")
        try:
            retire_recipe_generation(ctx.kitchen_id)
        except Exception:
            logger.warning("close_kitchen_recipe_generation_retirement_failed", exc_info=True)
    ctx.active_recipe_packs = None
    ctx.active_recipe_features = None
    ctx.active_recipe_steps = None
    ctx.active_recipe_ingredients = None
    ctx.session_serve_overrides = None
    ctx.session_serve_defer_unresolved = False
    ctx.recipe_name = ""
    ctx.recipe_content_hash = ""
    ctx.recipe_composite_hash = ""
    ctx.recipe_version = ""
    clear_recipe_execution(ctx)
    ctx.gate_infrastructure_ready = False
    logger.info("close_kitchen", gate_state="closed")
    if (log := ctx.github_api_log) is not None:
        orphan_usage = log.drain(ctx.kitchen_id)
        if orphan_usage is not None:
            try:
                log_dir = resolve_log_dir(ctx.config.linux_tracing.log_dir)
                orphan_path = log_dir / "github_api_usage_orchestrator.json"
                atomic_write(orphan_path, fast_dumps(orphan_usage))
            except Exception:
                logger.warning("close_kitchen_orphan_drain_failed", exc_info=True)
    review_gate_path = ctx.project_dir / ".autoskillit" / "temp" / "review_gate_state.json"
    try:
        try:
            state = json.loads(review_gate_path.read_text())
            loop_active = (
                isinstance(state, dict)
                and state.get("gate") == "LOOP_REQUIRED"
                and not state.get("check_review_loop_called", False)
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(
                "review_gate_state_read_failed", path=str(review_gate_path), error=str(exc)
            )
            loop_active = False
        if loop_active:
            logger.warning(
                "close_kitchen_review_gate_preserved",
                path=str(review_gate_path),
                reason="active_review_loop",
            )
        else:
            review_gate_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("review_gate_state_remove_failed", path=str(review_gate_path))
    with ctx.kitchen_transition_lock:
        context_id = ctx.kitchen_open_state.context_id
        ctx.kitchen_open_state = closed_kitchen_open_state(context_id=context_id)
        ctx.kitchen_id = ""


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("close_kitchen")
async def close_kitchen(ctx: Context = CurrentContext()) -> str:
    """Close the AutoSkillit kitchen.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("close_kitchen")) is not None:
            return h
        _close_kitchen_handler()
        from autoskillit.server import _get_ctx  # circular-break: server lifecycle owner

        exploration_store = _get_ctx().exploration_context_store
        if exploration_store is not None:
            exploration_store.close()

        mcp.disable(tags={"kitchen"})
        mcp.disable(tags={"exploration"})
        mcp.disable(tags={"plan-review"})

        await ctx.reset_visibility()
        return "Kitchen is closed."
    except Exception as exc:
        logger.error("close_kitchen unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
