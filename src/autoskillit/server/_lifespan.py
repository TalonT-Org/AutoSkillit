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
from contextlib import asynccontextmanager
from typing import Any

from autoskillit.core import cleanup_readiness_sentinel, get_logger, write_readiness_sentinel
from autoskillit.execution import RecordingSubprocessRunner
from autoskillit.server._state import _get_ctx_or_none, deferred_initialize

logger = get_logger(__name__)


def run_startup_drift_check() -> None:
    """Compare on-disk hooks.json hash vs HOOK_REGISTRY_HASH; regenerate if stale.

    Called as a background task from the lifespan. Any failure is logged and
    swallowed — drift must never prevent the server from starting.
    """
    try:
        import json

        import autoskillit.core.paths as _core_paths
        from autoskillit.core import atomic_write
        from autoskillit.hook_registry import (
            HOOK_REGISTRY_HASH,
            generate_hooks_json,
            load_hooks_json_hash,
        )

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
        from autoskillit.hook_registry import find_broken_hook_scripts, iter_all_scope_paths

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
    from autoskillit.core import sweep_retiring_cache  # noqa: PLC0415

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


async def _fleet_auto_gate_boot(ctx: Any) -> None:
    """Auto-open the kitchen gate and prime quota/registry state for fleet sessions.

    Called synchronously in _autoskillit_lifespan before yield, ensuring gate
    is open before any tool call arrives. Fails open: any step failure is
    logged as a warning and does not abort gate activation.
    """
    import os as _os
    from pathlib import Path
    from uuid import uuid4

    from autoskillit.core import register_active_kitchen
    from autoskillit.pipeline import create_background_task
    from autoskillit.server._misc import (
        _prime_quota_cache,
        _quota_refresh_loop,
    )
    from autoskillit.server.tools.tools_kitchen import _write_hook_config

    ctx.kitchen_id = str(uuid4())
    ctx.active_recipe_packs = frozenset()
    if ctx.gate is None:
        logger.warning("fleet_auto_gate_boot_no_gate")
        return
    ctx.gate.enable()
    logger.info("fleet_auto_gate_boot", gate_state="open", kitchen_id=ctx.kitchen_id)

    try:
        from autoskillit.core import _collect_disabled_feature_tags
        from autoskillit.server import mcp as _mcp

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
            _quota_refresh_loop(ctx.config.quota_guard),
            label="quota_refresh_loop",
        )
    except Exception:
        logger.warning("fleet_auto_gate_boot_quota_refresh_failed", exc_info=True)

    try:
        register_active_kitchen(ctx.kitchen_id, _os.getpid(), str(Path.cwd()))
    except Exception:
        logger.warning("fleet_auto_gate_boot_registry_failed", exc_info=True)


@asynccontextmanager
async def _autoskillit_lifespan(server: Any) -> Any:
    """Server lifecycle: write readiness sentinel, yield, then finalize recording.

    Readiness model: the sentinel file is written as the first statement inside
    the ``try:`` block. By the time the lifespan body runs,
    ``_serve_with_signal_guard()`` in ``cli/app.py`` has already armed the anyio
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
        from autoskillit.pipeline import create_background_task
        from autoskillit.server import _state

        event = _asyncio.Event()
        _state._startup_ready = event
        write_readiness_sentinel()
        bg_tasks.append(create_background_task(_run_drift_check_async(), label="drift_check"))
        bg_tasks.append(create_background_task(_run_retiring_sweep_async(), label="cache_sweep"))
        bg_tasks.append(
            create_background_task(_run_hook_health_check_async(), label="hook_health")
        )
        bg_tasks.append(create_background_task(_run_deferred_init(event), label="deferred_init"))
        from autoskillit.core import SessionType
        from autoskillit.core import session_type as _resolve_session_type

        if _resolve_session_type() is SessionType.FLEET:
            _fleet_ctx = _get_ctx_or_none()
            if _fleet_ctx is not None:
                await _fleet_auto_gate_boot(_fleet_ctx)
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
            from autoskillit.core import clear_kitchens_for_pid  # noqa: PLC0415

            clear_kitchens_for_pid(os.getpid())
        except Exception:
            logger.exception("lifespan kitchen registry cleanup error")
        try:
            _finalize_recorder()
        except Exception:
            logger.exception("lifespan recorder finalization error")
