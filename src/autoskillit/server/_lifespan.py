"""FastMCP lifespan for server resource teardown.

Provides the async context manager wired into FastMCP via ``lifespan=``.
The ``__aexit__`` side calls ``recorder.finalize()`` so scenario data survives
SIGTERM (issue #745).

Startup: runs a registry-hash drift check and self-heals hooks.json and
user-scope settings.json if the on-disk hash is stale.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from autoskillit.core import get_logger
from autoskillit.execution import RecordingSubprocessRunner
from autoskillit.server._state import _get_ctx_or_none

logger = get_logger(__name__)


def run_startup_drift_check() -> None:
    """Compare on-disk hooks.json hash vs HOOK_REGISTRY_HASH; regenerate if stale.

    Called at server startup. Any failure is logged and swallowed — drift must
    never prevent the server from starting.
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


@asynccontextmanager
async def _autoskillit_lifespan(server: Any) -> Any:
    """Server lifecycle: drift check on startup, teardown recording on shutdown."""
    logger.info("lifespan_started")
    run_startup_drift_check()
    try:
        yield
    finally:
        ctx = _get_ctx_or_none()
        if ctx is not None and isinstance(ctx.runner, RecordingSubprocessRunner):
            try:
                ctx.runner.recorder.finalize()
            except Exception:
                logger.exception("recorder.finalize() failed during lifespan teardown")
