"""Gate-enablement/transition handler extracted for testability.

Cross-submodule helpers are imported directly from their submodules
(``.._open_kitchen_transition``, ``.._open_kitchen_errors``,
``.._tracker_authority``) to avoid a circular-import hazard through the
package facade.
"""

from __future__ import annotations

from autoskillit.core import get_logger, sweep_stale_markers
from autoskillit.pipeline import transition_ambiguous, transition_confirm, transition_degraded
from autoskillit.server._guards import _backend_supports_quota
from autoskillit.server.tools import tools_kitchen as _tk_pkg
from autoskillit.server.tools.tools_kitchen._open_kitchen_errors import (
    _kitchen_failure_envelope,
)
from autoskillit.server.tools.tools_kitchen._open_kitchen_transition import (
    _ensure_kitchen_transition,
    _transition_start,
)
from autoskillit.server.tools.tools_kitchen._tracker_authority import (
    _register_active_recipe_kitchen,
    _retain_kitchen_tracker_authority,
    prune_stale_kitchen_state,
)

logger = get_logger(__name__)


async def _open_kitchen_handler(*, preserve_active_recipe: bool = False) -> str | None:
    """Set the tools-enabled flag. Extracted for testability.

    Returns ``None`` on success, or a JSON failure envelope string on error.
    """
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    _ensure_kitchen_transition(ctx)
    if _transition_start(ctx, "gate_enablement"):
        ctx.gate.enable()
        transition_confirm(
            ctx,
            "gate_enablement",
            receipt="gate:enabled",
            downstream_identity=ctx.kitchen_id,
        )
    if not preserve_active_recipe and _transition_start(ctx, "active_recipe_reset"):
        ctx.active_recipe_packs = frozenset()
        ctx.active_recipe_features = frozenset()
        ctx.active_recipe_steps = {}
        ctx.active_recipe_projection = None
        ctx.active_recipe_ingredients = frozenset()
        _tk_pkg.clear_recipe_execution(ctx)
        transition_confirm(ctx, "active_recipe_reset", receipt="active_recipe:cleared")
    logger.info("open_kitchen", gate_state="open", kitchen_id=ctx.kitchen_id)
    _supports_quota = _backend_supports_quota(ctx)

    if _transition_start(ctx, "hook_configuration"):
        try:
            _tk_pkg._write_hook_config()
        except Exception as exc:
            ctx.gate.disable()
            transition_ambiguous(ctx, "hook_configuration", exc)
            logger.warning("open_kitchen_failure", stage="write_hook_config", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="write_hook_config")
        transition_confirm(ctx, "hook_configuration", receipt="hook_config:written")

    if _transition_start(ctx, "quota_cache_prime"):
        try:
            await _tk_pkg._prime_quota_cache(supports_quota_check=_supports_quota)
        except Exception as exc:
            ctx.gate.disable()
            transition_ambiguous(ctx, "quota_cache_prime", exc)
            logger.warning("open_kitchen_failure", stage="prime_quota_cache", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="prime_quota_cache")
        transition_confirm(ctx, "quota_cache_prime", receipt="quota_cache:primed")

    if _transition_start(ctx, "quota_task_start"):
        if ctx.quota_refresh_task is not None:
            ctx.quota_refresh_task.cancel()
        try:
            ctx.quota_refresh_task = _tk_pkg.create_background_task(
                _tk_pkg._quota_refresh_loop(
                    ctx.config.quota_guard,
                    supports_quota_check=_supports_quota,
                ),
                label="quota_refresh_loop",
            )
        except Exception as exc:
            ctx.gate.disable()
            transition_ambiguous(ctx, "quota_task_start", exc)
            logger.warning("open_kitchen_failure", stage="start_quota_refresh", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="start_quota_refresh")
        transition_confirm(
            ctx,
            "quota_task_start",
            receipt="quota_task:owned",
            downstream_identity=str(id(ctx.quota_refresh_task)),
        )

    if _transition_start(ctx, "registry_update"):
        try:
            _retain_kitchen_tracker_authority(ctx)
            _register_active_recipe_kitchen(ctx)
        except Exception as exc:
            transition_degraded(ctx, "registry_update", exc)
            logger.warning("open_kitchen_registry_failed", exc_info=True)
        else:
            transition_confirm(
                ctx,
                "registry_update",
                receipt="registry:kitchen_registered",
                downstream_identity=ctx.kitchen_id,
            )

    if _transition_start(ctx, "tracker_prune"):
        try:
            prune_stale_kitchen_state(ctx.project_dir, ctx.kitchen_id)
        except Exception as exc:
            transition_degraded(ctx, "tracker_prune", exc)
            logger.warning("open_kitchen_prune_trackers_failed", exc_info=True)
        else:
            transition_confirm(ctx, "tracker_prune", receipt="trackers:pruned")

    if _transition_start(ctx, "marker_sweep"):
        try:
            sweep_stale_markers()
        except Exception as exc:
            transition_degraded(ctx, "marker_sweep", exc)
            logger.warning("open_kitchen_sweep_markers_failed", exc_info=True)
        else:
            transition_confirm(ctx, "marker_sweep", receipt="markers:swept")

    if _transition_start(ctx, "stale_dispatch_reap"):
        try:
            _campaign_state_paths = _tk_pkg.discover_campaign_state_files(ctx.project_dir)
            if _campaign_state_paths:
                await _tk_pkg.reap_stale_dispatches_async(
                    _campaign_state_paths,
                    min_reap_age_seconds=60.0,
                    heartbeat_grace_seconds=90.0,
                )
        except Exception as exc:
            transition_degraded(ctx, "stale_dispatch_reap", exc)
            logger.warning("open_kitchen_reap_failed", exc_info=True)
        else:
            transition_confirm(ctx, "stale_dispatch_reap", receipt="dispatches:reaped")

    if _transition_start(ctx, "tether_sweep"):
        try:
            from autoskillit.server._lifespan import (  # circular-break
                _reap_self_excluded_codex_and_daemon_orphans,
            )

            await _tk_pkg.sweep_orphaned_tethers_async(_tk_pkg.default_tether_dir())
            _reap_self_excluded_codex_and_daemon_orphans()
        except Exception as exc:
            transition_degraded(ctx, "tether_sweep", exc)
            logger.warning("open_kitchen_tether_sweep_failed", exc_info=True)
        else:
            transition_confirm(ctx, "tether_sweep", receipt="tethers:swept")

    ctx.gate_infrastructure_ready = True
    return None
