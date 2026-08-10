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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    installed_plugin_cache_dir,
    register_active_kitchen,
    resolve_kitchen_id,
    write_readiness_sentinel,
)
from autoskillit.core import (
    session_type as _resolve_session_type,
)
from autoskillit.execution import BACKEND_REGISTRY, RecordingSubprocessRunner
from autoskillit.fleet import (
    discover_campaign_state_files,
    reap_stale_dispatches_async,
    sweep_stale_dispatch_labels,
)
from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    find_broken_hook_scripts,
    iter_all_scope_paths,
    render_hooks_json_text,
    validate_plugin_cache_hooks,
)
from autoskillit.pipeline import (
    KitchenOpenPhase,
    OwnerBoundExplorationContextStore,
    confirm_kitchen_effect,
    create_background_task,
    new_kitchen_open_state,
    start_kitchen_effect,
)
from autoskillit.server._guards import _backend_supports_quota
from autoskillit.server._state import _get_ctx_or_none, deferred_initialize
from autoskillit.workspace import (
    PluginHookRepairStatus,
    read_obligation,
    repair_broken_plugin_cache_hooks,
    repair_broken_projection_hooks,
    verify_install_state,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend

logger = get_logger(__name__)


def run_startup_drift_check() -> None:
    """Compare on-disk hooks.json bytes vs current render; regenerate if stale.

    Unlike the previous hash-based gate, this byte comparison is
    content-complete: any difference in the rendered manifest — including
    command-string changes with a structurally identical registry — triggers
    a rewrite.  The hash comparison was blind to rendered command drift
    when the registry structure was unchanged (the Aug 2026 incident shape).

    Called as a background task from the lifespan.  Render and file-I/O
    failures are caught independently so a renderer regression cannot
    silently disable self-heal.
    """
    hooks_json_path = _core_paths.pkg_root() / "hooks" / "hooks.json"
    try:
        expected = render_hooks_json_text()
    except Exception:
        logger.exception("startup_drift_check_render_failed")
        return
    try:
        try:
            on_disk = hooks_json_path.read_text(encoding="utf-8")
        except OSError:
            on_disk = None
        if on_disk != expected:
            logger.info(
                "startup_drift_detected",
                reason="content_mismatch",
            )
            atomic_write(hooks_json_path, expected)
            logger.info("hooks_json_self_healed", path=str(hooks_json_path))
        else:
            logger.info("startup_drift_check_ok")
    except Exception:
        logger.exception("startup_drift_check_failed")


def _activate_recipe_kitchen(kitchen_id: str) -> None:
    """Publish one kitchen to the recipe-generation lifecycle."""
    from autoskillit.server._recipe_generation import activate_kitchen  # circular-break

    activate_kitchen(kitchen_id)


def run_startup_hook_health_check() -> list[str]:
    """Detect broken hook scripts across all settings scopes on MCP startup.

    Called as a background task alongside run_startup_drift_check().
    Returns list of broken hook commands. Any failure is logged and swallowed.

    On broken plugin-cache hooks OR a pending publication obligation, also
    attempts an in-process repair of the plugin cache's hook artifacts (the
    server must not shell out, per its existing design — see
    workspace._projected_artifact._hook_repair). This reduces the broken
    window while the obligation remains until a full verified install
    clears it; the in-process repair alone cannot perform that full
    publication, so it never clears the obligation itself.
    """
    broken: list[str] = []
    try:
        for scope_label, settings_path in iter_all_scope_paths(None):
            scope_broken = find_broken_hook_scripts(settings_path)
            if scope_broken:
                broken.extend(scope_broken)
                logger.warning(
                    "stale_hook_paths_detected",
                    scope=scope_label,
                    broken=scope_broken,
                )
        pending_obligation = read_obligation(Path.home())
    except Exception:
        logger.exception("startup_hook_health_check_failed")
        return []

    try:
        cache_broken = validate_plugin_cache_hooks()
        if cache_broken:
            broken.extend(cache_broken)
            logger.warning(
                "stale_plugin_cache_hooks_detected",
                broken=cache_broken,
                remediation="Run `autoskillit install` from an external terminal",
            )
    except Exception:
        logger.exception("startup_plugin_cache_hook_validation_failed")
        cache_broken = ["plugin cache hook validation failed"]

    if cache_broken or pending_obligation is not None:
        cache_dir = installed_plugin_cache_dir(Path.home(), "autoskillit")
        try:
            for outcome in repair_broken_plugin_cache_hooks(cache_dir):
                if outcome.status is PluginHookRepairStatus.REPAIRED:
                    logger.info(
                        "plugin_cache_hooks_repaired_at_startup",
                        incarnation=str(outcome.incarnation_dir),
                    )
                elif outcome.status is PluginHookRepairStatus.CONTENDED:
                    logger.warning(
                        "plugin_cache_hooks_repair_contended_at_startup",
                        incarnation=str(outcome.incarnation_dir),
                        reason=outcome.detail,
                    )
                else:
                    logger.error(
                        "plugin_cache_hooks_repair_failed_at_startup",
                        incarnation=str(outcome.incarnation_dir),
                        reason=outcome.detail,
                    )
        except Exception:
            logger.exception("startup_hook_repair_failed")

    # Projection repair — independent failure domain.  Must run even when the
    # plugin cache is healthy and no obligation is pending (projection-only
    # staleness).  NOT inside the cache_broken/pending_obligation gate above.
    try:
        for outcome in repair_broken_projection_hooks():
            if outcome.status is PluginHookRepairStatus.REPAIRED:
                logger.info(
                    "projection_hooks_repaired_at_startup",
                    incarnation=str(outcome.incarnation_dir),
                )
            elif outcome.status is PluginHookRepairStatus.CONTENDED:
                logger.warning(
                    "projection_hooks_repair_contended_at_startup",
                    incarnation=str(outcome.incarnation_dir),
                    reason=outcome.detail,
                )
            else:
                logger.error(
                    "projection_hooks_repair_failed_at_startup",
                    incarnation=str(outcome.incarnation_dir),
                    reason=outcome.detail,
                )
    except Exception:
        logger.exception("startup_projection_hook_repair_failed")

    # Codex config hook detection — detection-only (repair happens at sync time).
    try:
        from autoskillit.execution.backends._codex_hooks import find_broken_codex_hook_commands

        codex_broken = find_broken_codex_hook_commands()
        if codex_broken:
            broken.extend(codex_broken)
            logger.warning(
                "stale_codex_hook_commands_detected",
                broken=codex_broken,
                remediation="Run `autoskillit install` or re-sync Codex hooks",
            )
    except Exception:
        logger.exception("startup_codex_hook_detection_failed")

    return broken


def run_startup_install_state_check() -> list[str]:
    """Report install-state inconsistencies on MCP startup.

    The third consumer of ``verify_install_state()`` (alongside ``doctor`` and
    post-install verification), so the authority cannot decay into a function
    nobody calls. Diagnostic only: startup never fails on a finding, because
    the projection no longer depends on any of the artifacts being checked.
    Any failure is logged and swallowed.
    """
    try:
        findings = verify_install_state()
        for finding in findings:
            logger.warning(
                "install_state_inconsistent",
                check=finding.check,
                message=finding.message,
                remediation="Run `autoskillit install` from an external terminal",
            )
        return [f.check for f in findings]
    except Exception:
        logger.exception("startup_install_state_check_failed")
        return []


def run_startup_fix_required_coverage_check() -> None:
    """Validate that fix-required hook script stems are covered by at least one backend.

    The dispatch gate in tools_execution._check_backend_compat refuses all skill
    dispatches on a backend if HOOK_REGISTRY contains fix-required hooks whose
    script stems are not in that backend's applicable_guards. This check provides
    defense-in-depth: if the cross-registry invariant is violated, the server
    fails to start rather than accepting requests it will later crash on.

    Raises RuntimeError if any fix-required hook's script stems are not covered
    by the union of all registered backends' applicable_guards. A fix-required
    hook that IS covered by at least one backend is valid and does not raise.
    """
    all_guards: set[str] = set()
    for cls in BACKEND_REGISTRY.values():
        try:
            all_guards.update(cls().capabilities.applicable_guards)
        except Exception as exc:
            raise RuntimeError(
                f"Backend {cls.__name__!r} constructor raised during startup "
                f"fix-required coverage check: {exc}"
            ) from exc
    for h in HOOK_REGISTRY:
        if h.codex_status != "fix-required":
            continue
        stems = frozenset(Path(s).stem for s in h.scripts) if h.scripts else frozenset()
        if stems and not stems.issubset(all_guards):
            missing = sorted(stems - all_guards)
            raise RuntimeError(
                f"HOOK_REGISTRY fix-required entry (matcher={h.matcher!r}) has "
                f"guard scripts {missing} not covered by any backend's "
                f"applicable_guards. This will brick dispatch for backends "
                f"missing these guards."
            )


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
    ctx = _get_ctx_or_none()
    if ctx is None or ctx.plugin_retirement_coordinator is None:
        return
    loop = _asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        ctx.plugin_retirement_coordinator.sweep_due,
        datetime.now(UTC),
    )


async def _run_hook_health_check_async() -> None:
    """Offload blocking hook health check to a thread."""
    loop = _asyncio.get_running_loop()
    await loop.run_in_executor(None, run_startup_hook_health_check)


async def _run_install_state_check_async() -> None:
    """Offload the blocking install-state consistency check to a thread."""
    loop = _asyncio.get_running_loop()
    await loop.run_in_executor(None, run_startup_install_state_check)


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

    _supports_quota = _backend_supports_quota(ctx)

    try:
        await _prime_quota_cache(supports_quota_check=_supports_quota)
    except Exception:
        logger.warning("fleet_auto_gate_boot_prime_quota_cache_failed", exc_info=True)

    try:
        ctx.quota_refresh_task = create_background_task(
            _quota_refresh_loop(
                ctx.config.quota_guard,
                supports_quota_check=_supports_quota,
            ),
            label="quota_refresh_loop",
        )
    except Exception:
        logger.warning("fleet_auto_gate_boot_quota_refresh_failed", exc_info=True)

    try:
        register_active_kitchen(ctx.kitchen_id, os.getpid(), str(ctx.project_dir))
        _activate_recipe_kitchen(ctx.kitchen_id)
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

    with ctx.kitchen_transition_lock:
        state = ctx.kitchen_open_state
        if state.phase is KitchenOpenPhase.CLOSED:
            state = new_kitchen_open_state(
                kitchen_id=resolve_kitchen_id(),
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
    for tag in _collect_disabled_feature_tags(ctx.config.features, experimental_enabled=False):
        _mcp.disable(tags={tag})
    register_active_kitchen(ctx.kitchen_id, os.getpid(), str(ctx.project_dir))
    _activate_recipe_kitchen(ctx.kitchen_id)
    _write_hook_config()
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

    _supports_quota = _backend_supports_quota(ctx)

    try:
        await _prime_quota_cache(supports_quota_check=_supports_quota)
    except Exception:
        logger.warning("food_truck_auto_gate_boot_quota_cache_failed", exc_info=True)

    try:
        if ctx.config is not None:
            ctx.quota_refresh_task = create_background_task(
                _quota_refresh_loop(
                    ctx.config.quota_guard,
                    supports_quota_check=_supports_quota,
                ),
                label="quota_refresh_loop",
            )
    except Exception:
        logger.warning("food_truck_auto_gate_boot_refresh_loop_failed", exc_info=True)

    try:
        register_active_kitchen(ctx.kitchen_id, os.getpid(), str(ctx.project_dir))
        _activate_recipe_kitchen(ctx.kitchen_id)
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

        _supports_quota = _backend_supports_quota(ctx)
        await _prime_quota_cache(supports_quota_check=_supports_quota)
    except Exception:
        logger.warning("skill_auto_gate_boot_quota_cache_failed", exc_info=True)

    try:
        register_active_kitchen(ctx.kitchen_id, os.getpid(), str(ctx.project_dir))
        _activate_recipe_kitchen(ctx.kitchen_id)
    except Exception:
        logger.warning("skill_auto_gate_boot_registry_failed", exc_info=True)


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


async def _run_lifespan_session_boot(ctx: Any) -> None:
    """Apply exactly one authenticated explorer or ordinary session boot path."""
    if await _explorer_auto_gate_boot(ctx):
        return
    boot_fn = _LIFESPAN_BOOT_REGISTRY.get(_resolve_session_type())
    if boot_fn is not None:
        await boot_fn(ctx)


async def _run_backend_mcp_registration_async(backend: CodingAgentBackend) -> None:
    """Offload backend-owned MCP configuration to an executor — fail-open."""

    def _run_prelaunch() -> None:
        errors = backend.ensure_pre_launch()
        if errors:
            raise RuntimeError("; ".join(errors))

    try:
        loop = _asyncio.get_running_loop()
        await loop.run_in_executor(None, _run_prelaunch)
    except Exception:
        logger.warning("backend_mcp_registration_failed", exc_info=True)


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

        run_startup_fix_required_coverage_check()

        event = _asyncio.Event()
        _state._startup_ready = event
        write_readiness_sentinel()
        bg_tasks.append(create_background_task(_run_drift_check_async(), label="drift_check"))
        bg_tasks.append(create_background_task(_run_retiring_sweep_async(), label="cache_sweep"))
        bg_tasks.append(
            create_background_task(_run_hook_health_check_async(), label="hook_health")
        )
        bg_tasks.append(
            create_background_task(_run_install_state_check_async(), label="install_state")
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
                    _run_backend_mcp_registration_async(_boot_ctx.backend),
                    label="backend_mcp_registration",
                )
            )

        if _boot_ctx is not None:
            await _run_lifespan_session_boot(_boot_ctx)
        yield
    finally:
        for task in bg_tasks:
            if not task.done():
                task.cancel()
        if bg_tasks:
            try:
                await _asyncio.gather(*bg_tasks, return_exceptions=True)
            except _asyncio.CancelledError:
                pass  # don't let task cancellation bypass finalize_recorder
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
