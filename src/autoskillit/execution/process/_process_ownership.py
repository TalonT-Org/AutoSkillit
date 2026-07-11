"""Invocation-owned process identity tracker (issue #4233).

One tracker per ``run_managed_async`` invocation. Distinct from the
observed workload identity used by tracing/callbacks; this tracker
captures and retains canonical root + descendant identities so cleanup
can finalize them after root-first exit.

The tracker retains canonical PID/create-time pairs monotonically even
after the root has been reaped. Descendants forked after the last
identity refresh are still discovered because the tracker enumerates
the captured process group/session before finalize.

CPU baselines are root-local — descendants are observed via psutil
walks only at finalize time, never for cache priming.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import psutil

from autoskillit.core import CleanupOutcome, ProcessIdentity, get_logger

logger = get_logger(__name__)


@dataclass
class OwnedProcessIdentityTracker:
    """Per-invocation tracker for owned root + descendant process identities."""

    root_pid: int = 0
    root_start_time: float = 0.0
    process_group_id: int = 0
    session_id: int = 0
    captured: dict[int, float] = field(default_factory=dict)
    """Map of canonical PID -> create-time (seconds since epoch, POSIX)."""

    def register_root(
        self,
        pid: int,
        start_time: float,
        *,
        process_group_id: int | None = None,
        session_id: int | None = None,
    ) -> None:
        """Register the owned root PID/create-time immediately after spawn.

        Identity capture MUST happen before ``Process.wait()`` reaps the
        root, otherwise the post-reap enumeration cannot match it. PTY
        mode resolves the workload PID separately and passes it here.
        """
        if pid <= 0:
            raise ValueError("root_pid_must_be_positive")
        self.root_pid = pid
        self.root_start_time = start_time
        self.captured[pid] = start_time
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

    def add_descendant(self, pid: int, start_time: float) -> None:
        """Retain a descendant PID/create-time identity monotonically."""
        if pid <= 0 or pid == self.root_pid:
            return
        if pid not in self.captured:
            self.captured[pid] = start_time

    def snapshot_identities(self) -> tuple[ProcessIdentity, ...]:
        """Return a frozen tuple of every retained identity.

        The root identity is always first; descendant identities follow
        in PID order so output is deterministic across invocations.
        """
        ordered = sorted(self.captured.items(), key=lambda kv: kv[0])
        out: list[ProcessIdentity] = []
        for pid, create_time in ordered:
            out.append(
                ProcessIdentity(
                    root_pid=pid,
                    start_time=create_time,
                    process_group_id=self.process_group_id,
                    session_id=self.session_id,
                    descendants=tuple(
                        (d_pid, d_time) for d_pid, d_time in ordered if d_pid != pid
                    ),
                )
            )
        return tuple(out)

    def refresh_from_process_group(self) -> int:
        """Enumerate the captured process group/session and retain new identities.

        Returns the count of new identities added. PID-reused processes
        are skipped when their create-time does not match a captured
        entry; this protects against misidentifying an unrelated process
        that inherited the PID.
        """
        if self.process_group_id <= 0 and self.session_id <= 0:
            return 0
        added = 0
        try:
            current_pids = sorted(os.listdir(f"/proc/{self.root_pid}/task"))
        except (OSError, FileNotFoundError, ProcessLookupError):
            return 0
        for pid_str in current_pids:
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            try:
                _stat = os.stat(f"/proc/{pid}")
                create_time = _stat.st_ctime
            except (OSError, FileNotFoundError, ProcessLookupError):
                continue
            existing = self.captured.get(pid)
            if existing is None or abs(existing - create_time) > 0.01:
                self.captured[pid] = create_time
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


def is_pid_alive(pid: int, expected_create_time: float | None = None) -> bool:
    """Return True when ``pid`` is alive and (when provided) matches the create-time.

    Used by the shielded finalizer to verify identity before sending
    SIGTERM/SIGKILL. ``expected_create_time`` is the POSIX ctime from
    /proc/[pid]; PID reuse protection rejects processes whose create-time
    differs.
    """
    if pid <= 0:
        return False
    try:
        proc = psutil.Process(pid)
        if not psutil.pid_exists(pid):
            return False
        with proc.oneshot():
            create_time = proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    if expected_create_time is None:
        return True
    return abs(create_time - expected_create_time) < 0.5


def time_remaining(deadline: float) -> float:
    """Return remaining time until ``deadline`` (monotonic clock)."""
    return max(0.0, deadline - time.monotonic())
