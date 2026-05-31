"""Unified execution marker protocol for stale-detector suppression.

Async context manager that writes a ``{label}-in-progress-{session_id}-{uuid}.marker``
file, heartbeats its mtime, and deletes it on exit.  Lives in ``core/`` (IL-0) so
both ``fleet/_api.py`` (IL-2) and ``server/tools/`` (IL-3) can import it without
violating layer constraints.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio

from .io import write_versioned_json
from .logging import get_logger

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
    """Write, heartbeat, and clean up an execution marker.

    Yields the marker ``Path`` on success, or ``None`` when ``marker_dir`` is
    ``None`` or the initial write fails (suppression disabled, same semantics
    as ``fleet/_api.py``'s dispatch marker).
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

    async with anyio.create_task_group() as tg:
        tg.start_soon(_touch_marker, marker_path, heartbeat_interval)
        try:
            yield marker_path
        finally:
            tg.cancel_scope.cancel()
            try:
                marker_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "execution_marker_unlink_failed", marker=str(marker_path), exc_info=True
                )
