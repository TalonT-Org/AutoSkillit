"""Invocation-owned process identity tracker (issue #4233).

One tracker per ``run_managed_async`` invocation. Distinct from the
observed workload identity used by tracing/callbacks; this tracker
captures and retains canonical root + descendant identities so cleanup
can finalize them after root-first exit.

The tracker retains canonical PID/start-time identities monotonically even
after the root has been reaped. Descendants forked after the last
identity refresh are still discovered because the tracker enumerates
the captured process group/session before finalize.

CPU baselines are root-local — descendants are observed via psutil
walks only at finalize time, never for cache priming.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import psutil

from autoskillit.core import (
    CleanupOutcome,
    ProcessIdentity,
    get_logger,
    read_starttime_ticks,
)

logger = get_logger(__name__)


@dataclass
class OwnedProcessIdentityTracker:
    """Per-invocation tracker for owned root + descendant process identities."""

    root_pid: int = 0
    root_starttime_ticks: int = 0
    root_fallback_create_time: float = 0.0
    process_group_id: int = 0
    session_id: int = 0
    captured: dict[int, tuple[int, float]] = field(default_factory=dict)
    """Map of PID to ``(starttime_ticks, fallback_create_time)``."""

    def register_root(
        self,
        pid: int,
        starttime_ticks: int,
        fallback_create_time: float,
        *,
        process_group_id: int | None = None,
        session_id: int | None = None,
    ) -> None:
        """Register the owned root identity immediately after spawn.

        Identity capture MUST happen before ``Process.wait()`` reaps the
        root, otherwise the post-reap enumeration cannot match it. PTY
        mode resolves the workload PID separately and passes it here.
        """
        if pid <= 0:
            raise ValueError("root_pid_must_be_positive")
        self.root_pid = pid
        self.root_starttime_ticks = starttime_ticks
        self.root_fallback_create_time = fallback_create_time
        self.captured[pid] = (starttime_ticks, fallback_create_time)
        if process_group_id is not None:
            self.process_group_id = process_group_id
        if session_id is not None:
            self.session_id = session_id
        # Capture process group + session identity from /proc when available
        try:
            _pgid = os.getpgid(pid)
            if _pgid > 0:
                self.process_group_id = _pgid
        except (OSError, ProcessLookupError):
            pass
        try:
            _sid = os.getsid(pid)
            if _sid > 0:
                self.session_id = _sid
        except (OSError, ProcessLookupError):
            pass

    def add_descendant(
        self,
        pid: int,
        starttime_ticks: int,
        fallback_create_time: float,
    ) -> None:
        """Retain a descendant process identity monotonically."""
        if pid <= 0 or pid == self.root_pid:
            return
        if pid not in self.captured:
            self.captured[pid] = (starttime_ticks, fallback_create_time)

    def snapshot_identities(self) -> tuple[ProcessIdentity, ...]:
        """Return a frozen tuple of every retained identity.

        The root identity is always first; descendant identities follow
        in PID order so output is deterministic across invocations.
        """
        ordered = sorted(self.captured.items(), key=lambda kv: kv[0])
        out: list[ProcessIdentity] = []
        for pid, (starttime_ticks, fallback_create_time) in ordered:
            out.append(
                ProcessIdentity(
                    root_pid=pid,
                    starttime_ticks=starttime_ticks,
                    fallback_create_time=fallback_create_time,
                    process_group_id=self.process_group_id,
                    session_id=self.session_id,
                    descendants=tuple(
                        (d_pid, d_identity[0]) for d_pid, d_identity in ordered if d_pid != pid
                    ),
                )
            )
        return tuple(out)

    def refresh_from_process_group(self) -> int:
        """Enumerate the captured process group/session and retain new identities.

        Returns the count of new identities added. Existing PID keys remain
        bound to their original start-time identity; ``is_pid_alive`` performs the
        final PID-reuse check immediately before signal delivery.
        """
        if self.process_group_id <= 0 and self.session_id <= 0:
            return 0
        added = 0
        for process in psutil.process_iter(attrs=("pid", "create_time")):
            try:
                pid = int(process.info["pid"])
                pgid = os.getpgid(pid)
                sid = os.getsid(pid)
            except (OSError, ProcessLookupError, psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if pgid != self.process_group_id and sid != self.session_id:
                continue
            create_time = float(process.info["create_time"] or 0.0)
            starttime_ticks = read_starttime_ticks(pid) or 0
            if starttime_ticks <= 0 and create_time <= 0:
                continue
            existing = self.captured.get(pid)
            if existing is None:
                self.captured[pid] = (starttime_ticks, create_time)
                added += 1
        return added

    def finalize(
        self,
        *,
        succeeded: bool,
        budget_exhausted: bool,
    ) -> CleanupOutcome:
        """Build the frozen ``CleanupOutcome`` from retained identities.

        The result is independent of the tracker's own dicts; ``succeeded``
        and ``budget_exhausted`` are passed in by the shielded finalizer
        after it finishes signal/verify work.
        """
        return CleanupOutcome(
            succeeded=succeeded,
            budget_exhausted=budget_exhausted,
            retained_identities=self.snapshot_identities(),
        )


def make_tracker() -> OwnedProcessIdentityTracker:
    """Build a fresh per-invocation tracker."""
    return OwnedProcessIdentityTracker()


def is_pid_present(pid: int) -> bool:
    """Return whether a PID still has a process-table entry, including zombies."""
    return pid > 0 and psutil.pid_exists(pid)


def is_pid_alive(
    pid: int,
    expected_starttime_ticks: int = 0,
    expected_fallback_create_time: float = 0.0,
) -> bool:
    """Return whether ``pid`` exists and matches its captured identity.

    Linux compares raw start-time ticks exactly. This deliberately avoids
    ``psutil.Process.create_time()``, whose wall-clock value includes an
    uncached boot time that can shift under WSL clock synchronization.
    Other platforms use psutil create time as a fallback.
    """
    if not is_pid_present(pid):
        return False
    if expected_starttime_ticks > 0:
        return read_starttime_ticks(pid) == expected_starttime_ticks
    if expected_fallback_create_time <= 0:
        return False
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            create_time = proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return create_time == expected_fallback_create_time


def time_remaining(deadline: float, *, now: float) -> float:
    """Return remaining time until ``deadline`` (monotonic clock)."""
    return max(0.0, deadline - now)
