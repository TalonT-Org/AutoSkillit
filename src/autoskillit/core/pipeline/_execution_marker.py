"""Run-skill invocation markers for fabricated-completion attestation.

The run-skill marker attests a live invocation to the fabricated-completion guard,
keyed by the caller hook session id. Supervisor liveness uses operation leases.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio

from ..io.io import write_versioned_json
from ..logging import get_logger

__all__ = ["execution_marker"]

logger = get_logger(__name__)


async def _touch_marker(marker_path: Path, interval: float) -> None:
    try:
        marker_path.touch()
    except OSError:
        logger.warning("execution_marker: touch failed %s", marker_path, exc_info=True)
    while True:
        await anyio.sleep(interval)
        try:
            marker_path.touch()
        except OSError:
            logger.warning("execution_marker: touch failed %s", marker_path, exc_info=True)


@asynccontextmanager
async def execution_marker(
    marker_dir: Path | None,
    session_id: str,
    label: str,
    heartbeat_interval: float = 30.0,
) -> AsyncGenerator[Path | None]:
    """Write, heartbeat, and clean up a run-skill attestation marker.

    Yields the marker ``Path`` on success, or ``None`` when ``marker_dir`` is
    ``None`` or the initial write fails. The marker attests the invocation to
    the fabricated-completion guard; it does not signal supervisor liveness.
    """
    if marker_dir is None:
        yield None
        return

    marker_path = marker_dir / f"{label}-in-progress-{session_id}-{uuid.uuid4().hex[:8]}.marker"
    try:
        write_versioned_json(
            marker_path,
            {
                "label": label,
                "orchestrator_pid": os.getpid(),
                "session_id": session_id,
            },
            schema_version=1,
        )
    except OSError:
        logger.warning("execution_marker_write_failed", marker=str(marker_path), exc_info=True)
        yield None
        return

    hb_task: asyncio.Task[None] | None = None
    try:
        hb_task = asyncio.get_running_loop().create_task(
            _touch_marker(marker_path, heartbeat_interval)
        )
        yield marker_path
    finally:
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("execution_marker: heartbeat failed", exc_info=True)
        try:
            marker_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "execution_marker_unlink_failed", marker=str(marker_path), exc_info=True
            )
