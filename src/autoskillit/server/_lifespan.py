"""FastMCP lifespan for server resource teardown and deferred startup.

Provides the async context manager wired into FastMCP via ``lifespan=``.
The pre-yield section submits deferred startup work (recovery, audit loading,
stale cleanup, drift check) as background tasks so they run after the
transport opens, not on the critical startup path.
The ``__aexit__`` side calls ``recorder.finalize()`` so scenario data survives
SIGTERM (issue #745).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from autoskillit.core import get_logger
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


async def _async_drift_check() -> None:
    """Run drift check in background. Wraps the sync function in a thread executor."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_startup_drift_check)


@asynccontextmanager
async def _autoskillit_lifespan(server: Any) -> Any:
    """Server lifecycle: submit deferred work and teardown recording on shutdown."""
    logger.info("lifespan_started")
    ctx = _get_ctx_or_none()
    if ctx is not None and ctx.background is not None:
        import autoskillit.server._state as _state_mod

        _state_mod._startup_ready = asyncio.Event()
        ctx.background.submit(
            deferred_initialize(ctx, ready_event=_state_mod._startup_ready),
            label="deferred_initialize",
        )
        ctx.background.submit(
            _async_drift_check(),
            label="startup_drift_check",
        )
    try:
        yield
    finally:
        ctx = _get_ctx_or_none()
        if ctx is not None and isinstance(ctx.runner, RecordingSubprocessRunner):
            try:
                ctx.runner.recorder.finalize()
            except Exception:
                logger.exception("recorder.finalize() failed during lifespan teardown")
