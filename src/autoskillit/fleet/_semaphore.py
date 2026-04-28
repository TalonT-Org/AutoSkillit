"""FleetSemaphore: configurable semaphore implementing the FleetLock protocol."""

from __future__ import annotations

import asyncio


class FleetSemaphore:
    """Configurable semaphore implementing FleetLock for fleet dispatch concurrency."""

    def __init__(self, max_concurrent: int = 1) -> None:
        self._semaphore = asyncio.BoundedSemaphore(max_concurrent)
        self._active = 0
        self._max = max_concurrent

    def at_capacity(self) -> bool:
        return self._active >= self._max

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        self._active += 1

    def release(self) -> None:
        self._active -= 1
        self._semaphore.release()

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def max_concurrent(self) -> int:
        return self._max
