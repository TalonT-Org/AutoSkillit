"""Per-session-type async auto-gate boots for the FastMCP server lifespan.

Each ``_*_auto_gate_boot`` runs in ``_lifespan._run_lifespan_session_boot``
once the server context is constructed, before any tool call is dispatched.
Boots are mutually exclusive: ``_run_lifespan_session_boot`` picks exactly
one path based on session type and the presence of a sealed launch authority.
"""

from __future__ import annotations

import asyncio as _asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    DISPATCH_ID_ENV_VAR,
    EVIDENCE_READER_ENV_FORWARD_VARS,
    EVIDENCE_READER_TOOLS,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    HEADLESS_AUTO_GATE_ENV_VAR,
    HEADLESS_ENV_VAR,
    SessionType,
    get_logger,
)
from autoskillit.execution import (
    find_orphaned_autoskillit_daemons,
    find_orphaned_codex_processes,
    reap_orphaned_autoskillit_daemons,
    reap_orphaned_codex_processes,
)
from autoskillit.fleet import sweep_stale_dispatch_labels
from autoskillit.pipeline import (
    KitchenOpenPhase,
    OwnerBoundExplorationContextStore,
    confirm_kitchen_effect,
    get_kitchen_process_identity,
    new_kitchen_open_state,
    start_kitchen_effect,
)

# Late-binding for monkeypatch reach: tests patch
# "autoskillit.server._lifespan._get_ctx_or_none" (the package facade), so
# _get_ctx_or_none must be resolved via attribute access on the package at
# call time rather than imported by name into this submodule.
from autoskillit.server import _lifespan as _lifespan_pkg
from autoskillit.server._guards import _backend_supports_quota
from autoskillit.server._lifespan._startup_checks import (
    _activate_recipe_kitchen,
    _retain_context_tracker_authority,
)

logger = get_logger(__name__)


_CLEANUP_STALE_MAX_AGE = 86400


def _reap_self_excluded_codex_and_daemon_orphans() -> None:
    """Manual-only reapers, promoted to an automatic chokepoint.

    Self-excludes the calling process's own pid — the boot gate runs inside
    an autoskillit process itself, which must never be mistaken for one of
    the orphan classes it is sweeping.
    """
    self_pid = os.getpid()
    codex_orphans = [o for o in find_orphaned_codex_processes() if o.pid != self_pid]
    if codex_orphans:
        reap_orphaned_codex_processes(codex_orphans)
    daemon_orphans = [d for d in find_orphaned_autoskillit_daemons() if d.pid != self_pid]
    if daemon_orphans:
        reap_orphaned_autoskillit_daemons(daemon_orphans)


async def _cleanup_stale_loop(interval: float = 1800.0) -> None:
    """Periodically sweep stale session skill directories (defense-in-depth).

    Runs for the server lifetime. Sleep-first: deferred_initialize already
    ran cleanup_stale at startup. CancelledError from sleep propagates
    uncaught, terminating the loop cleanly.
    """
    loop = _asyncio.get_running_loop()
    while True:
        await _asyncio.sleep(interval)
        ctx = _lifespan_pkg._get_ctx_or_none()
        if ctx is not None and ctx.session_skill_manager is not None:
            try:
                removed = await loop.run_in_executor(
                    None,
                    lambda: ctx.session_skill_manager.cleanup_stale(  # type: ignore[union-attr]
                        max_age_seconds=_CLEANUP_STALE_MAX_AGE
                    ),
                )
                if removed:
                    logger.info("cleanup_stale_sweep", removed=removed)
            except Exception:
                logger.warning("cleanup_stale_loop_error", exc_info=True)
        try:
            # The only ceiling enforcement that reaches a headless child whose
            # spawner (this long-lived server) is still alive — boot gates
            # only run once, at process start.
            report = await _lifespan_pkg.sweep_orphaned_tethers_async(
                _lifespan_pkg.default_tether_dir()
            )
            if report.outcomes:
                logger.info(
                    "cleanup_stale_tether_sweep",
                    reaped=report.reaped_count,
                    total=len(report.outcomes),
                )
        except Exception:
            logger.warning("cleanup_stale_tether_sweep_error", exc_info=True)


async def _fleet_auto_gate_boot(ctx: Any) -> None:
    """Auto-open the kitchen gate and prime quota/registry state for fleet sessions.

    Called synchronously in _autoskillit_lifespan before yield, ensuring gate
    is open before any tool call arrives. Fails open: any step failure is
    logged as a warning and does not abort gate activation.
    """
    ctx.kitchen_id = _lifespan_pkg.resolve_kitchen_id()
    ctx.active_recipe_packs = frozenset()
    ctx.active_recipe_features = frozenset()
    ctx.active_recipe_steps = {}
    ctx.active_recipe_ingredients = frozenset()
    if ctx.gate is None:
        logger.warning("fleet_auto_gate_boot_no_gate")
        return
    ctx.gate.enable()
    logger.info("fleet_auto_gate_boot", gate_state="open", kitchen_id=ctx.kitchen_id)

    try:
        from autoskillit.server import mcp as _mcp  # circular-break
        from autoskillit.server._misc import (  # circular-break
            _prime_quota_cache,
            _quota_refresh_loop,
        )
        from autoskillit.server.tools import (  # circular-break
            tools_kitchen as _tk_fleet,
        )

        _features = ctx.config.features if ctx.config is not None else {}
        _exp_enabled = ctx.config.experimental_enabled if ctx.config is not None else False
        for _tag in _lifespan_pkg._collect_disabled_feature_tags(
            _features, experimental_enabled=_exp_enabled
        ):
            _mcp.disable(tags={_tag})
    except Exception:
        logger.warning("fleet_auto_gate_boot_feature_suppression_failed", exc_info=True)

    try:
        _tk_fleet._write_hook_config()
    except Exception:
        logger.warning("fleet_auto_gate_boot_write_hook_config_failed", exc_info=True)

    _supports_quota = _backend_supports_quota(ctx)

    try:
        await _prime_quota_cache(supports_quota_check=_supports_quota)
    except Exception:
        logger.warning("fleet_auto_gate_boot_prime_quota_cache_failed", exc_info=True)

    try:
        ctx.quota_refresh_task = _lifespan_pkg.create_background_task(
            _quota_refresh_loop(
                ctx.config.quota_guard,
                supports_quota_check=_supports_quota,
            ),
            label="quota_refresh_loop",
        )
    except Exception:
        logger.warning("fleet_auto_gate_boot_quota_refresh_failed", exc_info=True)

    try:
        _retain_context_tracker_authority(ctx)
        _lifespan_pkg.register_active_kitchen(get_kitchen_process_identity(ctx))
        _activate_recipe_kitchen(ctx.kitchen_id)
    except Exception:
        logger.warning("fleet_auto_gate_boot_registry_failed", exc_info=True)

    _campaign_state_paths: list[Path] = []
    try:
        _campaign_state_paths = _lifespan_pkg.discover_campaign_state_files(ctx.project_dir)
    except Exception:
        logger.warning("fleet_auto_gate_boot_state_discovery_failed", exc_info=True)

    if _campaign_state_paths:
        try:
            await _lifespan_pkg.reap_stale_dispatches_async(
                _campaign_state_paths,
                own_campaign_id=ctx.kitchen_id,
                min_reap_age_seconds=60.0,
                reaper_dispatch_id=os.environ.get("AUTOSKILLIT_DISPATCH_ID", ""),
                heartbeat_grace_seconds=90.0,
            )
        except Exception:
            logger.warning("fleet_auto_gate_boot_reap_failed", exc_info=True)

    try:
        await _lifespan_pkg.sweep_orphaned_tethers_async(_lifespan_pkg.default_tether_dir())
    except Exception:
        logger.warning("fleet_auto_gate_boot_tether_sweep_failed", exc_info=True)

    try:
        _lifespan_pkg._reap_self_excluded_codex_and_daemon_orphans()
    except Exception:
        logger.warning("fleet_auto_gate_boot_codex_daemon_reap_failed", exc_info=True)

    if _campaign_state_paths and ctx.github_client is not None:
        try:
            _lifespan_pkg.create_background_task(
                sweep_stale_dispatch_labels(_campaign_state_paths, ctx.github_client),
                label="startup_label_recovery_sweep",
            )
        except Exception:
            logger.warning("fleet_auto_gate_boot_label_recovery_failed", exc_info=True)


async def _pre_reveal_kitchen(ctx: Any) -> None:
    """Pre-reveal kitchen for non-notification backends (no tools/list_changed support)."""
    from autoskillit.server import mcp as _mcp  # circular-break
    from autoskillit.server._misc import _prime_quota_cache  # circular-break
    from autoskillit.server.tools import tools_kitchen as _tk_pre_reveal  # circular-break

    with ctx.kitchen_transition_lock:
        state = ctx.kitchen_open_state
        if state.phase is KitchenOpenPhase.CLOSED:
            state = new_kitchen_open_state(
                kitchen_id=_lifespan_pkg.resolve_kitchen_id(),
                context_id=state.context_id,
            )
            ctx.kitchen_open_state = state
        ctx.kitchen_id = state.kitchen_id
        ctx.kitchen_open_state = start_kitchen_effect(
            ctx.kitchen_open_state,
            "pre_reveal_bootstrap",
        )
    ctx.active_recipe_packs = frozenset()
    ctx.active_recipe_features = frozenset()
    ctx.active_recipe_steps = {}
    ctx.active_recipe_ingredients = frozenset()
    if ctx.gate is not None:
        ctx.gate.enable()

    _mcp.enable(tags={"kitchen"})
    _mcp.enable(tags={"plan-review"})

    for subset in ctx.config.subsets.disabled:
        _mcp.disable(tags={subset})
    for tag in _lifespan_pkg._collect_disabled_feature_tags(
        ctx.config.features, experimental_enabled=False
    ):
        _mcp.disable(tags={tag})
    try:
        _retain_context_tracker_authority(ctx)
        _lifespan_pkg.register_active_kitchen(get_kitchen_process_identity(ctx))
        _activate_recipe_kitchen(ctx.kitchen_id)
    except Exception:
        logger.warning("pre_reveal_kitchen_registry_failed", exc_info=True)
    _tk_pre_reveal._write_hook_config()
    _supports_quota = _backend_supports_quota(ctx)
    await _prime_quota_cache(supports_quota_check=_supports_quota)
    ctx.gate_infrastructure_ready = True
    with ctx.kitchen_transition_lock:
        ctx.kitchen_open_state = confirm_kitchen_effect(
            ctx.kitchen_open_state,
            "pre_reveal_bootstrap",
            receipt="pre_reveal:ready",
            downstream_identity=ctx.kitchen_id,
        )


async def _food_truck_auto_gate_boot(ctx: Any) -> None:
    """Auto-open gate for headless food truck (ORCHESTRATOR) sessions.

    Runs at lifespan startup when AUTOSKILLIT_HEADLESS=1 and
    AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS is set. No-ops for interactive
    ORCHESTRATOR sessions (open_kitchen handles the gate there).
    """
    from autoskillit.server._misc import (  # circular-break
        _prime_quota_cache,
        _quota_refresh_loop,
    )
    from autoskillit.server.tools import tools_kitchen as _tk_food_truck  # circular-break

    if os.environ.get(HEADLESS_ENV_VAR) != "1":
        if ctx.backend is not None and not ctx.backend.capabilities.supports_tool_list_changed:
            await _pre_reveal_kitchen(ctx)
        return
    _raw_tags = os.environ.get(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "")
    if not _raw_tags:
        return

    _packs = frozenset(p.strip() for p in _raw_tags.split(",") if p.strip())
    ctx.kitchen_id = _lifespan_pkg.resolve_kitchen_id()
    ctx.active_recipe_packs = _packs
    ctx.active_recipe_features = frozenset()
    ctx.active_recipe_steps = {}
    ctx.active_recipe_ingredients = frozenset()

    if ctx.gate is None:
        logger.warning("food_truck_auto_gate_boot_no_gate")
        return

    ctx.gate.enable()
    logger.info(
        "food_truck_auto_gate_boot",
        gate_state="open",
        kitchen_id=ctx.kitchen_id,
        packs=sorted(_packs),
    )

    try:
        from autoskillit.server import mcp as _mcp  # circular-break

        _features = ctx.config.features if ctx.config is not None else {}
        _exp_enabled = ctx.config.experimental_enabled if ctx.config is not None else False
        for _tag in _lifespan_pkg._collect_disabled_feature_tags(
            _features, experimental_enabled=_exp_enabled
        ):
            _mcp.disable(tags={_tag})
    except Exception:
        logger.warning("food_truck_auto_gate_boot_feature_suppression_failed", exc_info=True)

    try:
        _tk_food_truck._write_hook_config()
    except Exception:
        logger.warning("food_truck_auto_gate_boot_hook_config_failed", exc_info=True)

    _supports_quota = _backend_supports_quota(ctx)

    try:
        await _prime_quota_cache(supports_quota_check=_supports_quota)
    except Exception:
        logger.warning("food_truck_auto_gate_boot_quota_cache_failed", exc_info=True)

    try:
        if ctx.config is not None:
            ctx.quota_refresh_task = _lifespan_pkg.create_background_task(
                _quota_refresh_loop(
                    ctx.config.quota_guard,
                    supports_quota_check=_supports_quota,
                ),
                label="quota_refresh_loop",
            )
    except Exception:
        logger.warning("food_truck_auto_gate_boot_refresh_loop_failed", exc_info=True)

    try:
        _retain_context_tracker_authority(ctx)
        _lifespan_pkg.register_active_kitchen(get_kitchen_process_identity(ctx))
        _activate_recipe_kitchen(ctx.kitchen_id)
    except Exception:
        logger.warning("food_truck_auto_gate_boot_registry_failed", exc_info=True)

    try:
        _campaign_state_paths = _lifespan_pkg.discover_campaign_state_files(ctx.project_dir)
    except Exception:
        logger.warning("food_truck_auto_gate_boot_state_discovery_failed", exc_info=True)
        return

    if _campaign_state_paths:
        try:
            _skip: frozenset[str] | None = None
            _own_campaign_id: str | None = None
            try:
                _own_dispatch_id = os.environ.get(DISPATCH_ID_ENV_VAR, "")
                _skip = frozenset({_own_dispatch_id}) if _own_dispatch_id else None
                _own_campaign_id = os.environ.get(CAMPAIGN_ID_ENV_VAR, "") or None
            except Exception:
                logger.warning("food_truck_auto_gate_boot_self_exclusion_failed", exc_info=True)

            await _lifespan_pkg.reap_stale_dispatches_async(
                _campaign_state_paths,
                skip_dispatch_ids=_skip,
                own_campaign_id=_own_campaign_id,
                min_reap_age_seconds=60.0,
                reaper_dispatch_id=os.environ.get("AUTOSKILLIT_DISPATCH_ID", ""),
                heartbeat_grace_seconds=90.0,
            )
        except Exception:
            logger.warning("food_truck_auto_gate_boot_reap_failed", exc_info=True)

    try:
        await _lifespan_pkg.sweep_orphaned_tethers_async(_lifespan_pkg.default_tether_dir())
    except Exception:
        logger.warning("food_truck_auto_gate_boot_tether_sweep_failed", exc_info=True)

    try:
        _lifespan_pkg._reap_self_excluded_codex_and_daemon_orphans()
    except Exception:
        logger.warning("food_truck_auto_gate_boot_codex_daemon_reap_failed", exc_info=True)

    try:
        if _campaign_state_paths and ctx.github_client is not None:
            _lifespan_pkg.create_background_task(
                sweep_stale_dispatch_labels(_campaign_state_paths, ctx.github_client),
                label="startup_label_recovery_sweep",
            )
    except Exception:
        logger.warning("food_truck_auto_gate_boot_label_sweep_failed", exc_info=True)


async def _skill_auto_gate_boot(ctx: Any) -> None:
    """Auto-open gate for headless SKILL sessions.

    Runs at lifespan startup when AUTOSKILLIT_HEADLESS=1 and
    AUTOSKILLIT_HEADLESS_AUTO_GATE=1. No-ops for non-headless sessions.
    Omits quota-refresh loop and campaign state recovery — SKILL sessions
    are short-lived.
    """

    if os.environ.get(HEADLESS_ENV_VAR) != "1":
        if ctx.backend is not None and not ctx.backend.capabilities.supports_tool_list_changed:
            await _pre_reveal_kitchen(ctx)
        return
    if os.environ.get(HEADLESS_AUTO_GATE_ENV_VAR) != "1":
        return

    ctx.kitchen_id = _lifespan_pkg.resolve_kitchen_id()
    ctx.active_recipe_packs = frozenset()
    ctx.active_recipe_features = frozenset()
    ctx.active_recipe_steps = {}
    ctx.active_recipe_ingredients = frozenset()

    if ctx.gate is None:
        logger.warning("skill_auto_gate_boot_no_gate")
        return

    ctx.gate.enable()
    logger.info(
        "skill_auto_gate_boot",
        gate_state="open",
        kitchen_id=ctx.kitchen_id,
    )

    try:
        from autoskillit.server import mcp as _mcp  # circular-break

        _features = ctx.config.features if ctx.config is not None else {}
        _exp_enabled = ctx.config.experimental_enabled if ctx.config is not None else False
        for _tag in _lifespan_pkg._collect_disabled_feature_tags(
            _features, experimental_enabled=_exp_enabled
        ):
            _mcp.disable(tags={_tag})
    except Exception:
        logger.warning("skill_auto_gate_boot_feature_suppression_failed", exc_info=True)

    try:
        from autoskillit.server.tools import tools_kitchen as _tk_skill  # circular-break

        _tk_skill._write_hook_config()
    except Exception:
        logger.warning("skill_auto_gate_boot_hook_config_failed", exc_info=True)

    try:
        from autoskillit.server._misc import _prime_quota_cache  # circular-break

        _supports_quota = _backend_supports_quota(ctx)
        await _prime_quota_cache(supports_quota_check=_supports_quota)
    except Exception:
        logger.warning("skill_auto_gate_boot_quota_cache_failed", exc_info=True)

    try:
        _retain_context_tracker_authority(ctx)
        _lifespan_pkg.register_active_kitchen(get_kitchen_process_identity(ctx))
        _activate_recipe_kitchen(ctx.kitchen_id)
    except Exception:
        logger.warning("skill_auto_gate_boot_registry_failed", exc_info=True)

    try:
        await _lifespan_pkg.sweep_orphaned_tethers_async(_lifespan_pkg.default_tether_dir())
    except Exception:
        logger.warning("skill_auto_gate_boot_tether_sweep_failed", exc_info=True)


_LIFESPAN_BOOT_REGISTRY: dict[SessionType, Callable[[Any], Awaitable[None]] | None] = {
    SessionType.FLEET: _fleet_auto_gate_boot,
    SessionType.ORCHESTRATOR: _food_truck_auto_gate_boot,
    SessionType.SKILL: _skill_auto_gate_boot,
}


async def _explorer_auto_gate_boot(ctx: Any) -> bool:
    """Reveal only broker tools after the sealed explorer launch authority verifies."""
    from autoskillit.server import mcp  # circular-break

    store = ctx.exploration_context_store
    if not isinstance(store, OwnerBoundExplorationContextStore):
        return False
    if not store.validate_launch_environment() or ctx.gate is None:
        return False
    ctx.gate.enable()
    mcp.enable(tags={"exploration"}, components={"tool"}, only=True)
    return True


async def _evidence_reader_auto_gate_boot(ctx: Any) -> bool:
    """Reveal exactly the reader brokers for one complete startup identity."""
    from autoskillit.server import mcp  # circular-break
    from autoskillit.server._session_type import (  # circular-break
        _evidence_reader_binding_state,
    )

    binding_state = _evidence_reader_binding_state()
    if binding_state == "absent":
        return False
    if binding_state == "malformed" or ctx.gate is None:
        raise RuntimeError("evidence reader startup identity is malformed")
    from autoskillit.server.tools._evidence_reader import (  # circular-break
        validate_evidence_reader_startup,
    )

    environment = {name: os.environ[name] for name in EVIDENCE_READER_ENV_FORWARD_VARS}
    validate_evidence_reader_startup(ctx, environment)
    mcp.enable(tags={"evidence-reader"}, components={"tool"}, only=True)
    if {tool.name for tool in await mcp.list_tools()} != EVIDENCE_READER_TOOLS:
        raise RuntimeError("evidence reader startup tool projection is incomplete")
    ctx.gate.enable()
    return True
