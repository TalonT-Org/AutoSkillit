"""Unified execution marker protocol for stale-detector suppression.

Async context manager that writes a ``*-in-progress-{session_id}-{label}.marker``
file, heartbeats its mtime, and deletes it on exit.  Lives in ``core/`` (IL-0) so
both ``fleet/_api.py`` (IL-2) and ``server/tools/`` (IL-3) can import it without
violating layer constraints.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio

from .io import write_versioned_json
from .logging import get_logger

logger = get_logger(__name__)


async def _touch_marker(marker_path: Path, interval: float, trigger: anyio.Event | None) -> None:
    try:
        marker_path.touch()
    except OSError:
        logger.warning("execution_marker: touch failed %s", marker_path, exc_info=True)
    while trigger is None or not trigger.is_set():
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
    """Write, heartbeat, and clean up an execution marker.

    Yields the marker ``Path`` on success, or ``None`` when ``marker_dir`` is
    ``None`` or the initial write fails (suppression disabled, same semantics
    as ``fleet/_api.py``'s dispatch marker).
    """
    if marker_dir is None:
        yield None
        return

    marker_path = marker_dir / f"{label}-in-progress-{session_id}-{label}.marker"
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
    trigger = anyio.Event()
    try:
        hb_task = asyncio.get_running_loop().create_task(
            _touch_marker(marker_path, heartbeat_interval, trigger)
        )
        yield marker_path
    finally:
        trigger.set()
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            marker_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "execution_marker_unlink_failed", marker=str(marker_path), exc_info=True
            )
