"""Dispatch heartbeat helper — moved from fleet/_api.py (#4851).

The heartbeat file lives in ``dispatches_dir`` so the cross-campaign reaper can
discover it without any path threading. Background ``asyncio`` task touches
the file every ``heartbeat_interval`` seconds; cleanup unlinks the file.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from autoskillit.core import atomic_write, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def _dispatch_heartbeat(
    dispatches_dir: Path,
    dispatch_id: str,
    heartbeat_interval: float = 30.0,
) -> AsyncGenerator[Path | None]:
    """Write, heartbeat, and clean up a dispatch heartbeat file.

    Co-locates the heartbeat file with the dispatch state file in ``dispatches_dir``
    so the cross-campaign reaper can discover it without any path threading.
    Yields the heartbeat ``Path`` on success, or ``None`` if the initial write fails.
    """
    hb_path = dispatches_dir / f"dispatch-{dispatch_id}.heartbeat"
    try:
        hb_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(hb_path, "{}")
    except OSError:
        logger.warning("dispatch_heartbeat_write_failed", heartbeat=str(hb_path), exc_info=True)
        yield None
        return

    async def _touch_heartbeat() -> None:
        while True:
            await asyncio.sleep(heartbeat_interval)
            try:
                hb_path.touch()
            except OSError:
                logger.warning(
                    "dispatch_heartbeat_touch_failed",
                    heartbeat=str(hb_path),
                    exc_info=True,
                )

    hb_task: asyncio.Task[None] | None = None
    try:
        hb_task = asyncio.get_running_loop().create_task(_touch_heartbeat())
        yield hb_path
    finally:
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("dispatch_heartbeat_task_failed", exc_info=True)
        try:
            hb_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "dispatch_heartbeat_unlink_failed", heartbeat=str(hb_path), exc_info=True
            )
