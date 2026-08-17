"""FastMCP lifespan glue for the AutoSkillit server.

The async context manager wired into FastMCP via ``lifespan=`` lives here. The
pre-yield section submits deferred startup work (recovery, audit loading,
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
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    cleanup_readiness_sentinel,
    get_logger,
    write_readiness_sentinel,
)
from autoskillit.core import (
    session_type as _resolve_session_type,
)
from autoskillit.pipeline import create_background_task
from autoskillit.server._lifespan._session_boots import (
    _LIFESPAN_BOOT_REGISTRY,
    _cleanup_stale_loop,
    _evidence_reader_auto_gate_boot,
    _explorer_auto_gate_boot,
)
from autoskillit.server._lifespan._startup_checks import (
    _finalize_recorder,
    run_startup_drift_check,
    run_startup_fix_required_coverage_check,
    run_startup_hook_health_check,
    run_startup_install_state_check,
)
from autoskillit.server._state import _get_ctx_or_none, deferred_initialize

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend

logger = get_logger(__name__)


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


async def _run_lifespan_session_boot(ctx: Any) -> None:
    """Apply exactly one restricted-child or ordinary session boot path."""
    if await _evidence_reader_auto_gate_boot(ctx):
        return
    if await _explorer_auto_gate_boot(ctx):
        return
    boot_fn = _LIFESPAN_BOOT_REGISTRY.get(_resolve_session_type())
    if boot_fn is not None:
        await boot_fn(ctx)


async def _run_backend_mcp_registration_async(backend: CodingAgentBackend) -> None:
    """Offload backend-owned MCP configuration to an executor — fail-open."""

    def _run_prelaunch() -> None:
        readiness = backend.ensure_pre_launch()
        if readiness.errors:
            raise RuntimeError("; ".join(readiness.errors))

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
            if _boot_ctx is not None:
                from autoskillit.server.tools.tools_kitchen._tracker_authority import (  # noqa: E501 # circular-break
                    _release_kitchen_tracker_authority,
                )

                _release_kitchen_tracker_authority(
                    _boot_ctx,
                    unregister=True,
                    retire=True,
                )
        except Exception:
            logger.exception("lifespan kitchen tracker authority cleanup error")
        try:
            _finalize_recorder()
        except Exception:
            logger.exception("lifespan recorder finalization error")
