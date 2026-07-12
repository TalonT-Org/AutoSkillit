"""Process tree kill utilities for subprocess lifecycle management."""

from __future__ import annotations

import signal
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Any, TypeVar

import anyio
import anyio.abc
import psutil

from autoskillit.core import CleanupOutcome, KillReason, ProcessIdentity, get_logger
from autoskillit.execution.process._process_ownership import (
    OwnedProcessIdentityTracker,
    _IdentityStatus,
    inspect_pid_identity,
    signal_process_identity,
    time_remaining,
)

logger = get_logger(__name__)

_T = TypeVar("_T")
_CLEANUP_OUTCOME_TYPE = CleanupOutcome


def kill_process_tree(pid: int, timeout: float = 2.0) -> None:
    """Kill a process and all its descendants. SIGTERM → wait → SIGKILL.

    Uses psutil to find ALL descendants (not just same process group),
    handles NoSuchProcess gracefully (PID already dead or recycled).

    Args:
        pid: PID of the root process to kill.
        timeout: Seconds to wait between SIGTERM and SIGKILL.
    """
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    # Collect all children first (recursive)
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []

    # Include the parent in the kill list
    all_procs = children + [parent]

    # Send SIGTERM to all
    for proc in all_procs:
        try:
            proc.send_signal(signal.SIGTERM)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Wait for graceful shutdown
    _, alive = psutil.wait_procs(all_procs, timeout=timeout)

    # SIGKILL survivors
    for proc in alive:
        try:
            proc.send_signal(signal.SIGKILL)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Brief wait for kernel cleanup
    psutil.wait_procs(alive, timeout=1.0)


async def async_kill_process_tree(pid: int, timeout: float = 2.0) -> None:
    """Non-blocking wrapper around kill_process_tree for async callers."""
    await anyio.to_thread.run_sync(kill_process_tree, pid, timeout)


@dataclass(frozen=True, slots=True)
class _TerminationExecution:
    """Private facade carrier for one termination decision and cleanup result."""

    kill_reason: KillReason
    cleanup_outcome: CleanupOutcome


@dataclass(frozen=True, slots=True)
class _OwnershipPreflight:
    """Deadline-bounded classification used before choosing a kill action."""

    live_identities: tuple[ProcessIdentity, ...] = ()
    unknown_identities: tuple[ProcessIdentity, ...] = ()
    classification_failed: bool = False
    budget_exhausted: bool = False

    @property
    def has_live_or_unknown(self) -> bool:
        return bool(self.live_identities or self.unknown_identities or self.classification_failed)


@dataclass
class _TerminationSignalState:
    """Mutable private signal receipt for synchronous cleanup."""

    signaled: bool = False


class _CleanupBudgetExpired(Exception):
    """Internal control flow for one exhausted absolute cleanup deadline."""


def _make_emergency_cleanup_outcome() -> CleanupOutcome:
    """Build a fail-closed outcome through the unpatched core type."""
    return _CLEANUP_OUTCOME_TYPE(succeeded=False, budget_exhausted=True)


def _failed_ownership_preflight() -> _OwnershipPreflight:
    return _OwnershipPreflight(classification_failed=True, budget_exhausted=True)


@dataclass
class _OwnedProcessFinalizer:
    """Single-flight, cancellation-shielded cleanup for one managed invocation."""

    tracker: OwnedProcessIdentityTracker
    budget_seconds: float
    process: anyio.abc.Process | None = None
    owned_root_pid: int = 0
    outcome: CleanupOutcome | None = None
    failure: BaseException | None = None
    signaled: bool = False
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    _done: anyio.Event = field(default_factory=anyio.Event)
    _running: bool = False
    _deadline: float | None = None
    _verified_alive: dict[int, ProcessIdentity] = field(default_factory=dict)
    _emergency_outcome: CleanupOutcome = field(
        default_factory=_make_emergency_cleanup_outcome,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Seed raw ownership only; construction must never perform identity I/O."""
        pid = self.owned_root_pid
        if pid <= 0 and self.process is not None:
            pid = self.process.pid
        if pid <= 0:
            pid = self.tracker.root_pid
        if pid > 0:
            self.owned_root_pid = pid
            self.tracker.seed_root(
                pid,
                process_group_id=self.tracker.process_group_id or pid,
                session_id=self.tracker.session_id or pid,
            )

    @property
    def cleanup_deadline(self) -> float | None:
        """The one deadline shared by grace, signaling, waits, and verification."""
        return self._deadline

    def start_deadline(self, *, now: float | None = None) -> float:
        """Atomically start and return the invocation cleanup deadline."""
        if self._deadline is None:
            started_at = anyio.current_time() if now is None else now
            self._deadline = started_at + max(0.0, self.budget_seconds)
        return self._deadline

    def remaining_time(
        self,
        *,
        limit: float | None = None,
        now: float | None = None,
    ) -> float:
        """Return deadline budget, optionally capped for natural-exit grace."""
        deadline = self.start_deadline(now=now)
        current = anyio.current_time() if now is None else now
        remaining = time_remaining(deadline, now=current)
        return remaining if limit is None else min(max(0.0, limit), remaining)

    async def run(self) -> CleanupOutcome:
        with anyio.CancelScope(shield=True):
            async with self._lock:
                if self.outcome is not None:
                    return self.outcome
                leader = not self._running
                if leader:
                    self._running = True
                    self.start_deadline()

            if not leader:
                await self._done.wait()
                assert self.outcome is not None
                return self.outcome

            result = self._emergency_outcome
            try:
                result = await self._run_once()
            except BaseException as exc:
                with suppress(BaseException):
                    logger.error("owned_process_cleanup_failed", error=exc, exc_info=True)
                self.failure = exc
                try:
                    result = self._conservative_failure_outcome()
                except BaseException as outcome_exc:
                    with suppress(BaseException):
                        logger.error(
                            "owned_process_failure_outcome_failed",
                            error=outcome_exc,
                            exc_info=True,
                        )
            finally:
                self.outcome = result
                self._done.set()
            return result
        assert self.outcome is not None
        return self.outcome

    async def preflight(self) -> _OwnershipPreflight:
        """Refresh and classify ownership without delivering any signal."""
        self.start_deadline()
        try:
            await self._refresh()
            live: list[ProcessIdentity] = []
            unknown = list(self.tracker.snapshot_unknown_identities())
            if self._raw_root_reaped():
                unknown = [
                    identity for identity in unknown if identity.root_pid != self.tracker.root_pid
                ]
            for identity in self.tracker.snapshot_known_identities():
                status = await self._call_sync(inspect_pid_identity, identity)
                if status is _IdentityStatus.ALIVE:
                    live.append(identity)
                    self._verified_alive[identity.root_pid] = identity
                elif status is _IdentityStatus.UNKNOWN:
                    unknown.append(identity)
            return _OwnershipPreflight(
                live_identities=tuple(live),
                unknown_identities=tuple(unknown),
                budget_exhausted=self.remaining_time() <= 0,
            )
        except BaseException as exc:
            with suppress(BaseException):
                logger.error("owned_process_preflight_failed", error=exc, exc_info=True)
            try:
                return _OwnershipPreflight(
                    unknown_identities=self._snapshot_owned_identities(),
                    classification_failed=True,
                    budget_exhausted=self.remaining_time() <= 0,
                )
            except BaseException as snapshot_exc:
                with suppress(BaseException):
                    logger.error(
                        "owned_process_preflight_snapshot_failed",
                        error=snapshot_exc,
                        exc_info=True,
                    )
                return _failed_ownership_preflight()

    async def _run_once(self) -> CleanupOutcome:
        await self._call_sync(self.tracker.enrich_root_identity)
        await self._refresh()

        term_sent = await self._signal_raw_root(signal.SIGTERM)
        term_sent = await self._signal_known(signal.SIGTERM) or term_sent
        if term_sent:
            await self._wait_after_signal()

        await self._refresh()
        kill_sent = await self._signal_raw_root(signal.SIGKILL)
        kill_sent = await self._signal_known(signal.SIGKILL) or kill_sent
        if kill_sent:
            await self._wait_after_signal()

        return await self._verify()

    async def _call_sync(self, callback: Callable[..., _T], *args: object) -> _T:
        remaining = self.remaining_time()
        if remaining <= 0:
            raise _CleanupBudgetExpired
        with anyio.move_on_after(remaining, shield=True) as scope:
            result = await anyio.to_thread.run_sync(
                partial(callback, *args),
                abandon_on_cancel=True,
            )
        if scope.cancel_called:
            raise _CleanupBudgetExpired
        return result

    async def _refresh(self) -> None:
        await self._call_sync(self.tracker.refresh_from_process_group)

    async def _signal_known(self, signal_number: signal.Signals) -> bool:
        sent = False
        for identity in self.tracker.snapshot_known_identities():
            status = await self._call_sync(
                signal_process_identity,
                identity,
                signal_number,
            )
            if status is _IdentityStatus.ALIVE:
                sent = True
                self.signaled = True
        return sent

    async def _wait_after_signal(self) -> None:
        remaining = self.remaining_time()
        if remaining <= 0:
            raise _CleanupBudgetExpired
        wait_seconds = min(0.25, remaining / 2.0)
        if wait_seconds <= 0:
            raise _CleanupBudgetExpired
        if self.process is not None and self.process.returncode is None:
            with anyio.move_on_after(wait_seconds, shield=True):
                await self.process.wait()
        else:
            await anyio.sleep(wait_seconds)

    async def _signal_raw_root(self, signal_number: signal.Signals) -> bool:
        """Signal an unknown root only through its exact retained raw handle."""
        if self.tracker.root_identity_known:
            return False
        process = self.process
        if process is None or process.pid != self.owned_root_pid or process.returncode is not None:
            return False
        try:
            if signal_number == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass
        else:
            self.signaled = True
        return True

    def _raw_root_reaped(self) -> bool:
        process = self.process
        return bool(
            process is not None
            and process.pid == self.owned_root_pid == self.tracker.root_pid
            and process.returncode is not None
        )

    def _snapshot_owned_identities(self) -> tuple[ProcessIdentity, ...]:
        identities = self.tracker.snapshot_identities()
        if self._raw_root_reaped():
            identities = tuple(
                identity for identity in identities if identity.root_pid != self.tracker.root_pid
            )
        return identities

    async def _verify(self) -> CleanupOutcome:
        retained: list[ProcessIdentity] = []
        unknown = list(self.tracker.snapshot_unknown_identities())
        if self._raw_root_reaped():
            unknown = [
                identity for identity in unknown if identity.root_pid != self.tracker.root_pid
            ]
        for identity in self.tracker.snapshot_known_identities():
            status = await self._call_sync(inspect_pid_identity, identity)
            if status is _IdentityStatus.ALIVE:
                retained.append(identity)
                self._verified_alive[identity.root_pid] = identity
            elif status is _IdentityStatus.UNKNOWN:
                unknown.append(identity)
                self._verified_alive.pop(identity.root_pid, None)
            else:
                self._verified_alive.pop(identity.root_pid, None)
        exhausted = self.remaining_time() <= 0
        return CleanupOutcome(
            succeeded=not retained and not unknown and not exhausted,
            budget_exhausted=exhausted,
            retained_identities=tuple(retained),
            unknown_identities=tuple(unknown),
        )

    def _conservative_failure_outcome(self) -> CleanupOutcome:
        """Fail closed without claiming unverified identities are alive."""
        identities = self._snapshot_owned_identities()
        retained = tuple(
            identity for identity in identities if identity.root_pid in self._verified_alive
        )
        unknown = tuple(
            identity for identity in identities if identity.root_pid not in self._verified_alive
        )
        return CleanupOutcome(
            succeeded=False,
            budget_exhausted=self.remaining_time() <= 0,
            retained_identities=retained,
            unknown_identities=unknown,
        )


def _finalize_owned_process_sync(
    *,
    process: subprocess.Popen[Any],
    tracker: OwnedProcessIdentityTracker,
    budget_seconds: float,
    signal_state: _TerminationSignalState | None = None,
) -> CleanupOutcome:
    """Identity-validating synchronous cleanup for ``run_managed_sync``."""
    emergency_outcome = _make_emergency_cleanup_outcome()
    deadline = time.monotonic() + max(0.0, budget_seconds)
    receipt = signal_state if signal_state is not None else _TerminationSignalState()
    verified_alive: dict[int, ProcessIdentity] = {}
    tracker.seed_root(
        process.pid,
        process_group_id=tracker.process_group_id or process.pid,
        session_id=tracker.session_id or process.pid,
    )
    try:
        tracker.enrich_root_identity()
        tracker.refresh_from_process_group()

        term_sent = _signal_raw_root_sync(process, tracker, signal.SIGTERM, receipt)
        term_sent = _signal_known_sync(tracker, signal.SIGTERM, deadline, receipt) or term_sent
        if term_sent:
            _wait_sync_process(process, deadline, maximum=0.25)

        tracker.refresh_from_process_group()
        kill_sent = _signal_raw_root_sync(process, tracker, signal.SIGKILL, receipt)
        kill_sent = _signal_known_sync(tracker, signal.SIGKILL, deadline, receipt) or kill_sent
        if kill_sent:
            _wait_sync_process(process, deadline, maximum=0.25)
        return _verify_sync_identities(
            tracker,
            deadline,
            process=process,
            verified_alive=verified_alive,
        )
    except BaseException as exc:
        with suppress(BaseException):
            logger.error("owned_sync_process_cleanup_failed", error=exc, exc_info=True)
        try:
            return _sync_failure_outcome(
                process,
                tracker,
                deadline,
                verified_alive,
            )
        except BaseException as outcome_exc:
            with suppress(BaseException):
                logger.error(
                    "owned_sync_failure_outcome_failed",
                    error=outcome_exc,
                    exc_info=True,
                )
            return emergency_outcome


def _signal_known_sync(
    tracker: OwnedProcessIdentityTracker,
    signal_number: signal.Signals,
    deadline: float,
    signal_state: _TerminationSignalState,
) -> bool:
    sent = False
    for identity in tracker.snapshot_known_identities():
        if time.monotonic() >= deadline:
            raise _CleanupBudgetExpired
        status = signal_process_identity(identity, signal_number)
        if status is _IdentityStatus.ALIVE:
            sent = True
            signal_state.signaled = True
    return sent


def _signal_raw_root_sync(
    process: subprocess.Popen[Any],
    tracker: OwnedProcessIdentityTracker,
    signal_number: signal.Signals,
    signal_state: _TerminationSignalState,
) -> bool:
    """Signal an unknown sync root only through its exact retained Popen handle."""
    if (
        tracker.root_identity_known
        or process.pid != tracker.root_pid
        or process.returncode is not None
    ):
        return False
    try:
        if signal_number == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass
    else:
        signal_state.signaled = True
    return True


def _wait_sync_process(
    process: subprocess.Popen[Any],
    deadline: float,
    *,
    maximum: float,
) -> None:
    remaining = time_remaining(deadline, now=time.monotonic())
    if remaining <= 0:
        return
    wait_seconds = min(maximum, remaining / 2.0)
    if process.returncode is not None:
        time.sleep(wait_seconds)
        return
    try:
        process.wait(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        pass


def _verify_sync_identities(
    tracker: OwnedProcessIdentityTracker,
    deadline: float,
    *,
    process: subprocess.Popen[Any],
    verified_alive: dict[int, ProcessIdentity],
) -> CleanupOutcome:
    retained: list[ProcessIdentity] = []
    unknown: list[ProcessIdentity] = list(tracker.snapshot_unknown_identities())
    if process.pid == tracker.root_pid and process.returncode is not None:
        unknown = [identity for identity in unknown if identity.root_pid != tracker.root_pid]
    for identity in tracker.snapshot_known_identities():
        if time.monotonic() >= deadline:
            unknown.append(identity)
            continue
        status = inspect_pid_identity(identity)
        if status is _IdentityStatus.ALIVE:
            retained.append(identity)
            verified_alive[identity.root_pid] = identity
        elif status is _IdentityStatus.UNKNOWN:
            unknown.append(identity)
            verified_alive.pop(identity.root_pid, None)
        else:
            verified_alive.pop(identity.root_pid, None)
    exhausted = time.monotonic() >= deadline
    return CleanupOutcome(
        succeeded=not retained and not unknown and not exhausted,
        budget_exhausted=exhausted,
        retained_identities=tuple(retained),
        unknown_identities=tuple(unknown),
    )


def _sync_failure_outcome(
    process: subprocess.Popen[Any],
    tracker: OwnedProcessIdentityTracker,
    deadline: float,
    verified_alive: dict[int, ProcessIdentity],
) -> CleanupOutcome:
    identities = tracker.snapshot_identities()
    if process.pid == tracker.root_pid and process.returncode is not None:
        identities = tuple(
            identity for identity in identities if identity.root_pid != tracker.root_pid
        )
    retained = tuple(identity for identity in identities if identity.root_pid in verified_alive)
    unknown = tuple(identity for identity in identities if identity.root_pid not in verified_alive)
    return CleanupOutcome(
        succeeded=False,
        budget_exhausted=time.monotonic() >= deadline,
        retained_identities=retained,
        unknown_identities=unknown,
    )
