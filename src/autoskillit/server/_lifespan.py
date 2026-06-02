"""FastMCP lifespan for server resource teardown and deferred startup.

Provides the async context manager wired into FastMCP via ``lifespan=``.
The pre-yield section submits deferred startup work (recovery, audit loading,
stale cleanup, drift check) as background tasks so they run after the
transport opens, not on the critical startup path.
The ``__aexit__`` side calls ``recorder.finalize()`` so scenario data survives
SIGTERM (issue #745).

Readiness synchronization: the lifespan writes a filesystem sentinel at
``core.readiness.write_readiness_sentinel()`` as the first statement inside the
``try:`` block. Integration tests poll the sentinel path rather than parsing log
lines — file existence is atomic and has no string-parse race. The sentinel is
cleaned up in ``finally:`` before ``_finalize_recorder()`` runs.
"""

from __future__ import annotations

import asyncio as _asyncio
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import autoskillit.core.paths as _core_paths
from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    DISPATCH_ID_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    HEADLESS_AUTO_GATE_ENV_VAR,
    HEADLESS_ENV_VAR,
    SessionType,
    _collect_disabled_feature_tags,
    atomic_write,
    cleanup_readiness_sentinel,
    clear_kitchens_for_pid,
    get_logger,
    register_active_kitchen,
    resolve_kitchen_id,
    sweep_retiring_cache,
    write_readiness_sentinel,
)
from autoskillit.core import (
    session_type as _resolve_session_type,
)
from autoskillit.execution import (
    RecordingSubprocessRunner,
    ensure_codex_mcp_registered,
)
from autoskillit.fleet import (
    discover_campaign_state_files,
    reap_stale_dispatches_async,
    sweep_stale_dispatch_labels,
)
from autoskillit.hook_registry import (
    HOOK_REGISTRY_HASH,
    find_broken_hook_scripts,
    generate_hooks_json,
    iter_all_scope_paths,
    load_hooks_json_hash,
    validate_plugin_cache_hooks,
)
from autoskillit.pipeline import create_background_task
from autoskillit.server._state import _get_ctx_or_none, deferred_initialize

logger = get_logger(__name__)


def run_startup_drift_check() -> None:
    """Compare on-disk hooks.json hash vs HOOK_REGISTRY_HASH; regenerate if stale.

    Called as a background task from the lifespan. Any failure is logged and
    swallowed — drift must never prevent the server from starting.
    """
    try:
        import json

        hooks_json_path = _core_paths.pkg_root() / "hooks" / "hooks.json"
        on_disk_hash = load_hooks_json_hash(hooks_json_path)
        if on_disk_hash != HOOK_REGISTRY_HASH:
            logger.info(
                "startup_drift_detected",
                on_disk=on_disk_hash,
                expected=HOOK_REGISTRY_HASH,
            )
            atomic_write(
                hooks_json_path,
                json.dumps(generate_hooks_json(), indent=2) + "\n",
            )
            logger.info("hooks_json_self_healed", path=str(hooks_json_path))
        else:
            logger.info("startup_drift_check_ok")
    except Exception:
        logger.exception("startup_drift_check_failed")


def run_startup_hook_health_check() -> list[str]:
    """Detect broken hook scripts across all settings scopes on MCP startup.

    Called as a background task alongside run_startup_drift_check().
    Returns list of broken hook commands. Any failure is logged and swallowed.
    """
    try:
        broken: list[str] = []
        for scope_label, settings_path in iter_all_scope_paths(None):
            scope_broken = find_broken_hook_scripts(settings_path)
            if scope_broken:
                broken.extend(scope_broken)
                logger.warning(
                    "stale_hook_paths_detected",
                    scope=scope_label,
                    broken=scope_broken,
                )
        cache_broken = validate_plugin_cache_hooks()
        if cache_broken:
            broken.extend(cache_broken)
            logger.warning(
                "stale_plugin_cache_hooks_detected",
                broken=cache_broken,
                remediation="Run `autoskillit install` from an external terminal",
            )
        return broken
    except Exception:
        logger.exception("startup_hook_health_check_failed")
        return []


def _finalize_recorder() -> None:
    """Finalize the recording subprocess runner if one is active."""
    ctx = _get_ctx_or_none()
    if ctx is not None and isinstance(ctx.runner, RecordingSubprocessRunner):
        try:
            ctx.runner.recorder.finalize()
        except Exception:
            logger.exception("recorder.finalize() failed during lifespan teardown")


async def _run_drift_check_async() -> None:
    """Offload blocking drift check (file hashing + atomic_write) to a thread."""
    loop = _asyncio.get_running_loop()
    await loop.run_in_executor(None, run_startup_drift_check)


async def _run_retiring_sweep_async() -> None:
    """Offload blocking retiring cache sweep to a thread."""

    loop = _asyncio.get_running_loop()
    await loop.run_in_executor(None, sweep_retiring_cache)


async def _run_hook_health_check_async() -> None:
    """Offload blocking hook health check to a thread."""
    loop = _asyncio.get_running_loop()
    await loop.run_in_executor(None, run_startup_hook_health_check)


async def _run_deferred_init(ready_event: _asyncio.Event) -> None:
    """Run deferred_initialize, signalling *ready_event* when done."""
    ctx = _get_ctx_or_none()
    if ctx is not None:
        await deferred_initialize(ctx, ready_event=ready_event)
    else:
        ready_event.set()


_CLEANUP_STALE_MAX_AGE = 86400


async def _cleanup_stale_loop(interval: float = 1800.0) -> None:
    """Periodically sweep stale session skill directories (defense-in-depth).

    Runs for the server lifetime. Sleep-first: deferred_initialize already
    ran cleanup_stale at startup. CancelledError from sleep propagates
    uncaught, terminating the loop cleanly.
    """
    loop = _asyncio.get_running_loop()
    while True:
        await _asyncio.sleep(interval)
        ctx = _get_ctx_or_none()
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


async def _fleet_auto_gate_boot(ctx: Any) -> None:
    """Auto-open the kitchen gate and prime quota/registry state for fleet sessions.

    Called synchronously in _autoskillit_lifespan before yield, ensuring gate
    is open before any tool call arrives. Fails open: any step failure is
    logged as a warning and does not abort gate activation.
    """
    ctx.kitchen_id = resolve_kitchen_id()
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
            resolve_provider,
        )
        from autoskillit.server.tools.tools_kitchen import _write_hook_config  # circular-break

        _features = ctx.config.features if ctx.config is not None else {}
        _exp_enabled = ctx.config.experimental_enabled if ctx.config is not None else False
        for _tag in _collect_disabled_feature_tags(_features, experimental_enabled=_exp_enabled):
            _mcp.disable(tags={_tag})
    except Exception:
        logger.warning("fleet_auto_gate_boot_feature_suppression_failed", exc_info=True)

    try:
        _write_hook_config()
    except Exception:
        logger.warning("fleet_auto_gate_boot_write_hook_config_failed", exc_info=True)

    try:
        await _prime_quota_cache()
    except Exception:
        logger.warning("fleet_auto_gate_boot_prime_quota_cache_failed", exc_info=True)

    try:
        ctx.quota_refresh_task = create_background_task(
            _quota_refresh_loop(
                ctx.config.quota_guard,
                provider=resolve_provider(ctx.config.providers.default_provider),
            ),
            label="quota_refresh_loop",
        )
    except Exception:
        logger.warning("fleet_auto_gate_boot_quota_refresh_failed", exc_info=True)

    try:
        register_active_kitchen(ctx.kitchen_id, os.getpid(), str(ctx.project_dir))
    except Exception:
        logger.warning("fleet_auto_gate_boot_registry_failed", exc_info=True)

    _campaign_state_paths: list[Path] = []
    try:
        _campaign_state_paths = discover_campaign_state_files(ctx.project_dir)
    except Exception:
        logger.warning("fleet_auto_gate_boot_state_discovery_failed", exc_info=True)

    if _campaign_state_paths:
        try:
            await reap_stale_dispatches_async(
                _campaign_state_paths,
                own_campaign_id=ctx.kitchen_id,
                min_reap_age_seconds=60.0,
                reaper_dispatch_id=os.environ.get("AUTOSKILLIT_DISPATCH_ID", ""),
                heartbeat_grace_seconds=90.0,
            )
        except Exception:
            logger.warning("fleet_auto_gate_boot_reap_failed", exc_info=True)

    if _campaign_state_paths and ctx.github_client is not None:
        try:
            create_background_task(
                sweep_stale_dispatch_labels(_campaign_state_paths, ctx.github_client),
                label="startup_label_recovery_sweep",
            )
        except Exception:
            logger.warning("fleet_auto_gate_boot_label_recovery_failed", exc_info=True)


async def _pre_reveal_kitchen(ctx: Any) -> None:
    """Pre-reveal kitchen for non-notification backends (no tools/list_changed support)."""
    from autoskillit.server import mcp as _mcp  # circular-break
    from autoskillit.server._misc import _prime_quota_cache  # circular-break
    from autoskillit.server.tools.tools_kitchen import _write_hook_config  # circular-break

    ctx.kitchen_id = resolve_kitchen_id()
    ctx.active_recipe_packs = frozenset()
    ctx.active_recipe_features = frozenset()
    ctx.active_recipe_steps = {}
    ctx.active_recipe_ingredients = frozenset()
    if ctx.gate is not None:
        ctx.gate.enable()
    for subset in ctx.config.subsets.disabled:
        _mcp.disable(tags={subset})
    for tag in _collect_disabled_feature_tags(ctx.config.features, experimental_enabled=False):
        _mcp.disable(tags={tag})
    register_active_kitchen(ctx.kitchen_id, os.getpid(), str(ctx.project_dir))
    _write_hook_config()
    await _prime_quota_cache()


async def _food_truck_auto_gate_boot(ctx: Any) -> None:
    """Auto-open gate for headless food truck (ORCHESTRATOR) sessions.

    Runs at lifespan startup when AUTOSKILLIT_HEADLESS=1 and
    AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS is set. No-ops for interactive
    ORCHESTRATOR sessions (open_kitchen handles the gate there).
    """
    from autoskillit.server._misc import (  # circular-break
        _prime_quota_cache,
        _quota_refresh_loop,
        resolve_provider,
    )
    from autoskillit.server.tools.tools_kitchen import _write_hook_config  # circular-break

    if os.environ.get(HEADLESS_ENV_VAR) != "1":
        if ctx.backend is not None and not ctx.backend.capabilities.supports_tool_list_changed:
            await _pre_reveal_kitchen(ctx)
        return
    _raw_tags = os.environ.get(FOOD_TRUCK_TOOL_TAGS_ENV_VAR, "")
    if not _raw_tags:
        return

    _packs = frozenset(p.strip() for p in _raw_tags.split(",") if p.strip())
    ctx.kitchen_id = resolve_kitchen_id()
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
        for _tag in _collect_disabled_feature_tags(_features, experimental_enabled=_exp_enabled):
            _mcp.disable(tags={_tag})
    except Exception:
        logger.warning("food_truck_auto_gate_boot_feature_suppression_failed", exc_info=True)

    try:
        _write_hook_config()
    except Exception:
        logger.warning("food_truck_auto_gate_boot_hook_config_failed", exc_info=True)

    try:
        await _prime_quota_cache()
    except Exception:
        logger.warning("food_truck_auto_gate_boot_quota_cache_failed", exc_info=True)

    try:
        if ctx.config is not None:
            ctx.quota_refresh_task = create_background_task(
                _quota_refresh_loop(
                    ctx.config.quota_guard,
                    provider=resolve_provider(ctx.config.providers.default_provider),
                ),
                label="quota_refresh_loop",
            )
    except Exception:
        logger.warning("food_truck_auto_gate_boot_refresh_loop_failed", exc_info=True)

    try:
        register_active_kitchen(ctx.kitchen_id, os.getpid(), str(ctx.project_dir))
    except Exception:
        logger.warning("food_truck_auto_gate_boot_registry_failed", exc_info=True)

    try:
        _campaign_state_paths = discover_campaign_state_files(ctx.project_dir)
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

            await reap_stale_dispatches_async(
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
        if _campaign_state_paths and ctx.github_client is not None:
            create_background_task(
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

    ctx.kitchen_id = resolve_kitchen_id()
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
        for _tag in _collect_disabled_feature_tags(_features, experimental_enabled=_exp_enabled):
            _mcp.disable(tags={_tag})
    except Exception:
        logger.warning("skill_auto_gate_boot_feature_suppression_failed", exc_info=True)

    try:
        from autoskillit.server.tools.tools_kitchen import _write_hook_config  # circular-break

        _write_hook_config()
    except Exception:
        logger.warning("skill_auto_gate_boot_hook_config_failed", exc_info=True)

    try:
        from autoskillit.server._misc import _prime_quota_cache  # circular-break

        await _prime_quota_cache()
    except Exception:
        logger.warning("skill_auto_gate_boot_quota_cache_failed", exc_info=True)

    try:
        register_active_kitchen(ctx.kitchen_id, os.getpid(), str(ctx.project_dir))
    except Exception:
        logger.warning("skill_auto_gate_boot_registry_failed", exc_info=True)


_LIFESPAN_BOOT_REGISTRY: dict[SessionType, Callable[[Any], Awaitable[None]] | None] = {
    SessionType.FLEET: _fleet_auto_gate_boot,
    SessionType.ORCHESTRATOR: _food_truck_auto_gate_boot,
    SessionType.SKILL: _skill_auto_gate_boot,
}


async def _run_codex_mcp_registration_async() -> None:
    """Offload ensure_codex_mcp_registered() to a thread executor — fail-open."""
    try:
        loop = _asyncio.get_running_loop()
        written = await loop.run_in_executor(None, ensure_codex_mcp_registered)
        if written:
            logger.warning("codex_mcp_registration_repaired_at_runtime")
    except Exception:
        logger.warning("codex_mcp_registration_failed", exc_info=True)


@asynccontextmanager
async def _autoskillit_lifespan(server: Any) -> Any:
    """Server lifecycle: write readiness sentinel, yield, then finalize recording.

    Readiness model: the sentinel file is written as the first statement inside
    the ``try:`` block. By the time the lifespan body runs,
    ``serve_with_signal_guard()`` in ``cli/_serve_guard.py`` has already armed the anyio
    signal receiver via ``tg.start()``. A SIGTERM delivered after the sentinel
    appears is guaranteed to be caught by the armed receiver — no race window.

    Background tasks (drift check, deferred init) are launched via
    ``create_background_task`` (from ``pipeline.background``) so they run
    concurrently without wrapping the ``yield`` in a task group.  A task-group
    ``yield`` causes a cancel-scope mismatch when FastMCP resumes the generator
    on a different task at exit.

    Teardown model: ``CancelledError`` from the anyio cancel scope unwinds past
    the ``yield``, triggering ``finally:``. Background tasks are cancelled,
    the sentinel is cleaned up, then ``_finalize_recorder()`` writes
    ``scenario.json``. Any teardown exception is logged and suppressed so the
    process exits cleanly.
    """
    bg_tasks: list[_asyncio.Task[None]] = []
    try:
        from autoskillit.server import _state  # circular-break

        event = _asyncio.Event()
        _state._startup_ready = event
        write_readiness_sentinel()
        bg_tasks.append(create_background_task(_run_drift_check_async(), label="drift_check"))
        bg_tasks.append(create_background_task(_run_retiring_sweep_async(), label="cache_sweep"))
        bg_tasks.append(
            create_background_task(_run_hook_health_check_async(), label="hook_health")
        )
        bg_tasks.append(create_background_task(_run_deferred_init(event), label="deferred_init"))
        bg_tasks.append(create_background_task(_cleanup_stale_loop(), label="cleanup_stale"))
        _boot_ctx = _get_ctx_or_none()

        if (
            _boot_ctx is not None
            and _boot_ctx.backend is not None
            and _boot_ctx.backend.capabilities.mcp_config_capable
        ):
            bg_tasks.append(
                create_background_task(
                    _run_codex_mcp_registration_async(),
                    label="codex_mcp_registration",
                )
            )

        _boot_fn = _LIFESPAN_BOOT_REGISTRY.get(_resolve_session_type())
        if _boot_fn is not None and _boot_ctx is not None:
            await _boot_fn(_boot_ctx)
        yield
    finally:
        for task in bg_tasks:
            if not task.done():
                task.cancel()
        if bg_tasks:
            await _asyncio.gather(*bg_tasks, return_exceptions=True)
        try:
            cleanup_readiness_sentinel()
        except Exception:
            logger.exception("lifespan sentinel cleanup error")
        try:
            clear_kitchens_for_pid(os.getpid())
        except Exception:
            logger.exception("lifespan kitchen registry cleanup error")
        try:
            _finalize_recorder()
        except Exception:
            logger.exception("lifespan recorder finalization error")
