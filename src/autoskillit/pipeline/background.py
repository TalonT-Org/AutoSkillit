"""Supervised background task execution for the server layer."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoskillit.core import atomic_write, get_logger

logger = get_logger(__name__)


class BackgroundTaskSupervisor:
    """Single entry point for supervised background tasks.

    All tasks submitted here are wrapped in exception capture, audit
    recording, and status file write. No submitted coroutine can fail
    silently.
    """

    def __init__(
        self,
        audit: Any | None = None,
        log: Any | None = None,
    ) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._audit = audit
        self._log = log or logger

    @property
    def pending_count(self) -> int:
        return len(self._tasks)

    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        on_exception: Callable[[Exception], None] | None = None,
        status_path: Path | None = None,
        label: str = "",
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(
            self._supervise_task(
                coro,
                on_exception=on_exception,
                status_path=status_path,
                label=label,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _supervise_task(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        on_exception: Callable[[Exception], None] | None,
        status_path: Path | None,
        label: str,
    ) -> Any:
        try:
            result = await coro
            return result
        except asyncio.CancelledError:
            if status_path is not None:
                _write_status(status_path, "cancelled")
            raise
        except Exception as exc:
            self._log.error(
                "background_task_failed",
                label=label,
                error=str(exc),
                exc_info=exc,
            )
            if status_path is not None:
                _write_status(status_path, "failed", error=str(exc))
            if self._audit is not None:
                try:
                    from autoskillit.core import FailureRecord, RetryReason

                    self._audit.record_failure(
                        FailureRecord(
                            timestamp=datetime.now(UTC).isoformat(),
                            skill_command=label[:200],
                            exit_code=-1,
                            subtype="background_exception",
                            needs_retry=False,
                            retry_reason=RetryReason.NONE,
                            stderr=str(exc)[:500],
                        )
                    )
                except Exception:
                    self._log.debug("audit.record_failure raised", exc_info=True)
            if on_exception is not None:
                try:
                    on_exception(exc)
                except Exception:
                    self._log.debug("on_exception callback raised", exc_info=True)

    async def drain(self) -> None:
        """Await all pending tasks to completion (for shutdown and tests)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


def _write_status(path: Path, status: str, *, error: str | None = None) -> None:
    """Write a status.json file atomically. Never raises."""
    try:
        payload: dict[str, Any] = {
            "status": status,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        if error is not None:
            payload["error"] = error
        atomic_write(path, json.dumps(payload, indent=2))
    except Exception:
        logger.debug("_write_status failed", path=str(path), exc_info=True)
