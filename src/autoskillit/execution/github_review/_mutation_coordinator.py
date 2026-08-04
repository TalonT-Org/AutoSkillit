"""Persisted lease and pacing coordination for GitHub review mutations."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Awaitable, Callable

from .ledger import GitHubReviewLedger, MutationSlot


class GitHubReviewMutationCoordinator:
    """Capacity-one persisted lease with atomic pacing revalidation."""

    def __init__(
        self,
        *,
        ledger: GitHubReviewLedger,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        minimum_interval_seconds: float = 1.0,
        lease_ttl_seconds: float = 60.0,
        busy_retry_seconds: float = 0.01,
    ) -> None:
        if lease_ttl_seconds <= 30.0:
            raise ValueError("review mutation lease TTL must exceed the HTTP timeout")
        self.ledger = ledger
        self.clock = clock
        self.sleeper = sleeper
        self.minimum_interval_seconds = minimum_interval_seconds
        self.lease_ttl_seconds = lease_ttl_seconds
        self.busy_retry_seconds = busy_retry_seconds

    async def acquire(
        self,
        *,
        scope_id: str,
        operation_key: str,
        lease_owner: str,
    ) -> MutationSlot:
        while True:
            try:
                slot = self.ledger.claim_mutation_slot(
                    scope_id=scope_id,
                    lease_owner=lease_owner,
                    operation_key=operation_key,
                    now=self.clock(),
                    minimum_interval_seconds=self.minimum_interval_seconds,
                    lease_ttl_seconds=self.lease_ttl_seconds,
                )
            except sqlite3.OperationalError as exc:
                if "busy" not in str(exc).casefold() and "locked" not in str(exc).casefold():
                    raise
                await self.sleeper(self.busy_retry_seconds)
                continue
            if slot.blocked_operation_key is not None or slot.ready:
                return slot
            await self.sleeper(slot.delay)
