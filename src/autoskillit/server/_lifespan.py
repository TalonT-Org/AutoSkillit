"""FastMCP lifespan for server resource teardown.

Provides the async context manager wired into FastMCP via ``lifespan=``.
The ``__aexit__`` side calls ``recorder.finalize()`` so scenario data survives
SIGTERM (issue #745).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from autoskillit.core import get_logger
from autoskillit.execution import RecordingSubprocessRunner
from autoskillit.server._state import _get_ctx_or_none

logger = get_logger(__name__)


@asynccontextmanager
async def _autoskillit_lifespan(server: Any) -> Any:
    """Server lifecycle: teardown recording on shutdown."""
    yield
    ctx = _get_ctx_or_none()
    if ctx is not None and isinstance(ctx.runner, RecordingSubprocessRunner):
        try:
            ctx.runner.recorder.finalize()
        except Exception:
            logger.exception("recorder.finalize() failed during lifespan teardown")
