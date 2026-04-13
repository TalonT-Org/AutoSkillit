"""FastMCP lifespan for server resource teardown and deferred startup.

Provides the async context manager wired into FastMCP via ``lifespan=``.
The pre-yield section submits deferred startup work (recovery, audit loading,
stale cleanup, drift check) as background tasks so they run after the
transport opens, not on the critical startup path.
The ``__aexit__`` side calls ``recorder.finalize()`` so scenario data survives
SIGTERM (issue #745).

The registry-hash drift check (``run_startup_drift_check``) is invoked from
``serve()`` in ``cli/app.py`` before ``mcp.run()``, not inside this lifespan,
so that SIGTERM received during the check cannot bypass teardown.

Readiness synchronization: the lifespan writes a filesystem sentinel at
``core.readiness.write_readiness_sentinel()`` as the first statement inside the
``try:`` block. Integration tests poll the sentinel path rather than parsing log
lines — file existence is atomic and has no string-parse race. The sentinel is
cleaned up in ``finally:`` before ``_finalize_recorder()`` runs.
"""

from __future__ import annotations

import asyncio
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


def _finalize_recorder() -> None:
    """Finalize the recording subprocess runner if one is active."""
    ctx = _get_ctx_or_none()
    if ctx is not None and isinstance(ctx.runner, RecordingSubprocessRunner):
        try:
            ctx.runner.recorder.finalize()
        except Exception:
            logger.exception("recorder.finalize() failed during lifespan teardown")


@asynccontextmanager
async def _autoskillit_lifespan(server: Any) -> Any:
    """Server lifecycle: write readiness sentinel, yield, then finalize recording.

    Readiness model: the sentinel file is written as the first statement inside
    the ``try:`` block. By the time the lifespan body runs,
    ``_serve_with_signal_guard()`` in ``cli/app.py`` has already armed the anyio
    signal receiver via ``tg.start()``. A SIGTERM delivered after the sentinel
    appears is guaranteed to be caught by the armed receiver — no race window.

    Teardown model: ``CancelledError`` from the anyio cancel scope unwinds past
    the ``yield``, triggering ``finally:``. The sentinel is cleaned up first,
    then ``_finalize_recorder()`` writes ``scenario.json``. Any teardown
    exception is logged and suppressed so the process exits cleanly.
    """
    try:
        write_readiness_sentinel()
        yield
    finally:
        try:
            cleanup_readiness_sentinel()
            _finalize_recorder()
        except Exception:
            logger.exception("lifespan teardown error")
