"""Process tree kill utilities for subprocess lifecycle management."""

from __future__ import annotations

import signal
from dataclasses import dataclass, field

import anyio
import anyio.abc
import psutil

from autoskillit.core import CleanupOutcome, get_logger
from autoskillit.execution.process._process_ownership import (
    OwnedProcessIdentityTracker,
    is_pid_alive,
    is_pid_present,
    time_remaining,
)

logger = get_logger(__name__)


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


@dataclass
class _OwnedProcessFinalizer:
    """Single-flight shielded cleanup for one managed invocation."""

    tracker: OwnedProcessIdentityTracker
    budget_seconds: float
    outcome: CleanupOutcome | None = None
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    _deadline: float | None = None

    async def run(self) -> CleanupOutcome:
        async with self._lock:
            if self.outcome is not None:
                return self.outcome
            self.tracker.refresh_from_process_group()
            budget = max(0.01, self.budget_seconds)
            if self._deadline is None:
                self._deadline = anyio.current_time() + budget
            deadline = self._deadline
            with anyio.move_on_after(
                time_remaining(deadline, now=anyio.current_time()), shield=True
            ) as scope:
                identities = self.tracker.snapshot_identities()
                root = next(
                    (item for item in identities if item.root_pid == self.tracker.root_pid),
                    None,
                )
                if root is not None and is_pid_alive(
                    root.root_pid,
                    root.starttime_ticks,
                    root.fallback_create_time,
                ):
                    root_budget = time_remaining(deadline, now=anyio.current_time())
                    if root_budget > 0:
                        await async_kill_process_tree(
                            root.root_pid,
                            timeout=min(1.0, root_budget),
                        )
                elif root is not None and is_pid_present(root.root_pid):
                    logger.error(
                        "owned_root_identity_mismatch",
                        pid=root.root_pid,
                        expected_starttime_ticks=root.starttime_ticks,
                    )
                self.tracker.refresh_from_process_group()
                remaining_identities = [
                    item
                    for item in self.tracker.snapshot_identities()
                    if item.root_pid != self.tracker.root_pid
                    and is_pid_alive(
                        item.root_pid,
                        item.starttime_ticks,
                        item.fallback_create_time,
                    )
                ]
                async with anyio.create_task_group() as tg:
                    for identity in remaining_identities:
                        remaining_budget = time_remaining(deadline, now=anyio.current_time())
                        if remaining_budget <= 0:
                            break
                        tg.start_soon(
                            async_kill_process_tree,
                            identity.root_pid,
                            min(0.25, remaining_budget),
                        )
            retained = []
            unknown = []
            for identity in self.tracker.snapshot_identities():
                if is_pid_alive(
                    identity.root_pid,
                    identity.starttime_ticks,
                    identity.fallback_create_time,
                ):
                    retained.append(identity)
                elif is_pid_present(identity.root_pid):
                    unknown.append(identity)
            self.outcome = CleanupOutcome(
                succeeded=not retained and not unknown and not scope.cancel_called,
                budget_exhausted=scope.cancel_called,
                retained_identities=tuple(retained),
                unknown_identities=tuple(unknown),
            )
            return self.outcome
