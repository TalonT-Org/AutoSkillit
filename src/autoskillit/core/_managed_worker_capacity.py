"""One process-wide, owner-bound capacity authority for managed workers."""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from collections.abc import Hashable
from dataclasses import dataclass


class ManagedWorkerCapacityError(RuntimeError):
    """Raised when a capacity permit is missing, foreign, or already released."""


@dataclass(frozen=True, slots=True)
class ManagedWorkerPermit:
    """Opaque authority returned for one exact owner acquisition."""

    permit_id: str
    _authority_id: str
    _owner: Hashable


@dataclass(slots=True)
class _Waiter:
    owner: Hashable
    future: asyncio.Future[ManagedWorkerPermit]


class DefaultManagedWorkerCapacity:
    """Bounded shared capacity with exact owner/token release semantics.

    The authority is deliberately reconfigured in place.  Existing permits stay
    valid across a kitchen configuration or close transition; lowering the
    limit merely prevents new admissions until enough owners release.
    """

    def __init__(self, max_concurrent: int = 1, timeout: float | None = None) -> None:
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be > 0, got {max_concurrent}")
        if timeout is not None and timeout <= 0:
            raise ValueError(f"timeout must be > 0 when provided, got {timeout}")
        self._authority_id = uuid.uuid4().hex
        self._max = max_concurrent
        self._timeout = timeout
        self._permits: dict[str, ManagedWorkerPermit] = {}
        self._owner_permits: dict[Hashable, ManagedWorkerPermit] = {}
        self._waiters: deque[_Waiter] = deque()
        self._waiting_owners: set[Hashable] = set()
        self._lock = threading.RLock()

    @staticmethod
    def _validate_owner(owner: object) -> Hashable:
        if isinstance(owner, str) and owner:
            return owner
        if (
            isinstance(owner, tuple)
            and owner
            and all(isinstance(part, str) and part for part in owner)
        ):
            return owner
        raise ValueError("managed worker owner must be a non-empty string or tuple of strings")

    def at_capacity(self) -> bool:
        with self._lock:
            return len(self._permits) >= self._max

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._permits)

    @property
    def max_concurrent(self) -> int:
        with self._lock:
            return self._max

    @property
    def timeout(self) -> float | None:
        with self._lock:
            return self._timeout

    def reconfigure(self, *, max_concurrent: int, timeout: float | None) -> None:
        """Update admission settings without discarding active owner debt."""
        if max_concurrent <= 0:
            raise ValueError(f"max_concurrent must be > 0, got {max_concurrent}")
        if timeout is not None and timeout <= 0:
            raise ValueError(f"timeout must be > 0 when provided, got {timeout}")
        with self._lock:
            self._max = max_concurrent
            self._timeout = timeout
            self._admit_waiters_locked()

    def restore_owner_debt(self, owner: object, permit_id: str) -> ManagedWorkerPermit:
        """Restore durable unresolved ownership before startup reconciliation.

        This is intentionally narrower than ``acquire``: only the recovery
        service may turn an exact persisted permit identity back into capacity.
        It never wakes waiters or infers that a process is gone.
        """
        normalized_owner = self._validate_owner(owner)
        if not isinstance(permit_id, str) or not permit_id:
            raise ValueError("persisted managed worker permit_id must be non-empty")
        with self._lock:
            existing = self._permits.get(permit_id)
            if existing is not None:
                if existing._owner != normalized_owner:
                    raise ManagedWorkerCapacityError("persisted permit owner conflicts")
                return existing
            if normalized_owner in self._owner_permits:
                raise ManagedWorkerCapacityError("persisted owner already holds a permit")
            return self._grant_locked(normalized_owner, permit_id=permit_id)

    async def acquire(self, owner: object) -> ManagedWorkerPermit:
        """Acquire one permit for ``owner``, timing out without leaving debt."""
        normalized_owner = self._validate_owner(owner)
        with self._lock:
            if normalized_owner in self._owner_permits or normalized_owner in self._waiting_owners:
                raise ManagedWorkerCapacityError("owner already holds or awaits a permit")
            if len(self._permits) < self._max:
                return self._grant_locked(normalized_owner)
            loop = asyncio.get_running_loop()
            waiter = _Waiter(normalized_owner, loop.create_future())
            self._waiters.append(waiter)
            self._waiting_owners.add(normalized_owner)
            timeout = self._timeout
        try:
            if timeout is None:
                return await waiter.future
            # Do not shield: timeout must cancel the inner future so the
            # scheduled `_deliver` callback observes ``future.cancelled() is
            # True`` and runs the ``self.release(token)`` recovery branch,
            # rather than leaking the permit when a grant races the timeout.
            return await asyncio.wait_for(waiter.future, timeout=timeout)
        except BaseException:
            with self._lock:
                self._remove_waiter_locked(waiter)
                # Python 3.12+ asyncio.wait_for uses timeouts.timeout; if
                # _deliver landed in the same loop batch as the timeout
                # handle, set_result completed the future BEFORE the
                # cancel, leaving a permit in _permits/_owner_permits with
                # no owner. Reclaim it explicitly. (3.11 used a different
                # implementation that returned the result without raising,
                # so this branch is 3.12+-only in practice.)
                if (
                    waiter.future.done()
                    and not waiter.future.cancelled()
                    and waiter.future.exception() is None
                ):
                    self.release(waiter.future.result())
            raise

    def release(self, permit: ManagedWorkerPermit) -> None:
        """Release exactly the same token returned by :meth:`acquire`."""
        if not isinstance(permit, ManagedWorkerPermit):
            raise ManagedWorkerCapacityError("managed worker release requires a permit token")
        with self._lock:
            known = self._permits.get(permit.permit_id)
            if known is None or known is not permit or permit._authority_id != self._authority_id:
                raise ManagedWorkerCapacityError(
                    "permit is foreign, duplicated, or already released"
                )
            del self._permits[permit.permit_id]
            del self._owner_permits[permit._owner]
            self._admit_waiters_locked()

    def _grant_locked(
        self, owner: Hashable, *, permit_id: str | None = None
    ) -> ManagedWorkerPermit:
        permit = ManagedWorkerPermit(
            permit_id=permit_id or f"managed-permit-{uuid.uuid4().hex}",
            _authority_id=self._authority_id,
            _owner=owner,
        )
        self._permits[permit.permit_id] = permit
        self._owner_permits[owner] = permit
        return permit

    def _remove_waiter_locked(self, waiter: _Waiter) -> None:
        self._waiting_owners.discard(waiter.owner)
        try:
            self._waiters.remove(waiter)
        except ValueError:
            pass

    def _admit_waiters_locked(self) -> None:
        while self._waiters and len(self._permits) < self._max:
            waiter = self._waiters.popleft()
            self._waiting_owners.discard(waiter.owner)
            if waiter.future.done():
                continue
            permit = self._grant_locked(waiter.owner)
            loop = waiter.future.get_loop()

            def _deliver(
                future: asyncio.Future[ManagedWorkerPermit] = waiter.future,
                token: ManagedWorkerPermit = permit,
            ) -> None:
                if future.cancelled():
                    self.release(token)
                elif not future.done():
                    future.set_result(token)

            loop.call_soon_threadsafe(_deliver)


__all__ = [
    "DefaultManagedWorkerCapacity",
    "ManagedWorkerCapacityError",
    "ManagedWorkerPermit",
]
