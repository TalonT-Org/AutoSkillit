"""Bounded ownership for fleet campaign-state mutation."""

from __future__ import annotations

import fcntl
import signal
from _thread import LockType
from contextlib import ExitStack
from pathlib import Path
from typing import IO

from autoskillit.core import acquire_flock_with_timeout, get_logger

logger = get_logger(__name__)


class CampaignStateMutatorOwnership:
    """Own one campaign mutation's process lock and flock descriptor."""

    def __init__(self) -> None:
        self._cleanup = ExitStack()

    def acquire(
        self,
        lock_path: Path,
        *,
        process_lock: LockType,
        timeout: float,
    ) -> IO[bytes]:
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        try:
            try:
                if not process_lock.acquire(timeout=timeout):
                    raise TimeoutError(
                        f"Timed out acquiring in-process fleet state lock after {timeout} seconds"
                    )
                try:
                    self._cleanup.callback(process_lock.release)
                except BaseException:
                    process_lock.release()
                    raise
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                flock_handle = open(lock_path, "wb")

                def close_flock_handle() -> None:
                    try:
                        flock_handle.close()
                    except Exception:
                        logger.debug(
                            "CampaignStateMutatorOwnership.close: flock close failed",
                            exc_info=True,
                        )

                try:
                    self._cleanup.callback(close_flock_handle)
                except BaseException:
                    flock_handle.close()
                    raise
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

            acquire_flock_with_timeout(
                flock_handle.fileno(),
                operation=fcntl.LOCK_EX,
                timeout=timeout,
                path=lock_path,
            )
            return flock_handle
        except BaseException:
            self._cleanup.close()
            raise

    def close(self) -> None:
        self._cleanup.close()
