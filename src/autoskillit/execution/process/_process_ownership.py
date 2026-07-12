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
from enum import Enum
from signal import Signals

import psutil

from autoskillit.core import (
    CleanupOutcome,
    ProcessIdentity,
    get_logger,
    read_starttime_ticks,
)

logger = get_logger(__name__)


class _IdentityStatus(Enum):
    """Result of checking one retained PID identity."""

    ALIVE = "alive"
    ABSENT = "absent"
    UNKNOWN = "unknown"


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
    unknown_pids: set[int] = field(default_factory=set)
    """Seeded PIDs for which canonical identity enrichment did not succeed."""

    def seed_root(
        self,
        pid: int,
        *,
        process_group_id: int | None = None,
        session_id: int | None = None,
    ) -> None:
        """Retain the raw spawn identity without performing process-table I/O.

        Managed callers spawn with ``start_new_session=True``, so they may seed
        the process group and session with the spawn PID before any fallible
        lookup. Canonical start-time/create-time enrichment happens separately.
        """
        if pid <= 0:
            raise ValueError("root_pid_must_be_positive")
        self.root_pid = pid
        if process_group_id is not None:
            self.process_group_id = process_group_id
        if session_id is not None:
            self.session_id = session_id
        if pid not in self.captured:
            self.captured[pid] = (0, 0.0)
            self.unknown_pids.add(pid)

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

        This method only records already-resolved values and performs no I/O.
        Identity capture MUST happen before ``Process.wait()`` reaps the root,
        otherwise post-reap enumeration cannot match it.
        """
        if pid <= 0:
            raise ValueError("root_pid_must_be_positive")
        self.root_pid = pid
        self.root_starttime_ticks = starttime_ticks
        self.root_fallback_create_time = fallback_create_time
        self.captured[pid] = (starttime_ticks, fallback_create_time)
        if starttime_ticks > 0 or fallback_create_time > 0:
            self.unknown_pids.discard(pid)
        else:
            self.unknown_pids.add(pid)
        if process_group_id is not None:
            self.process_group_id = process_group_id
        if session_id is not None:
            self.session_id = session_id

    def enrich_root_identity(self) -> bool:
        """Resolve the seeded root identity without leaking lookup failures.

        A quick exit or access denial leaves the root explicitly unknown. The
        finalizer can then use only its retained raw process handle, never PID-
        directed signaling for that root.
        """
        pid = self.root_pid
        if pid <= 0:
            return False
        existing = self.captured.get(pid, (0, 0.0))
        if existing[0] > 0 or existing[1] > 0:
            return True

        try:
            starttime_ticks = read_starttime_ticks(pid) or 0
            process = psutil.Process(pid)
            with process.oneshot():
                fallback_create_time = process.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            self.captured[pid] = (0, 0.0)
            self.unknown_pids.add(pid)
            return False

        if starttime_ticks <= 0 and fallback_create_time <= 0:
            self.captured[pid] = (0, 0.0)
            self.unknown_pids.add(pid)
            return False

        process_group_id = self.process_group_id
        session_id = self.session_id
        try:
            resolved_group_id = os.getpgid(pid)
            if resolved_group_id > 0:
                process_group_id = resolved_group_id
        except (OSError, ProcessLookupError):
            pass
        try:
            resolved_session_id = os.getsid(pid)
            if resolved_session_id > 0:
                session_id = resolved_session_id
        except (OSError, ProcessLookupError):
            pass
        self.register_root(
            pid,
            starttime_ticks,
            fallback_create_time,
            process_group_id=process_group_id,
            session_id=session_id,
        )
        return True

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
            if starttime_ticks <= 0 and fallback_create_time <= 0:
                self.unknown_pids.add(pid)

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

    def snapshot_known_identities(self) -> tuple[ProcessIdentity, ...]:
        """Return retained identities that are safe to validate before signaling."""
        return tuple(
            identity
            for identity in self.snapshot_identities()
            if identity.root_pid not in self.unknown_pids
            and (identity.starttime_ticks > 0 or identity.fallback_create_time > 0)
        )

    def snapshot_unknown_identities(self) -> tuple[ProcessIdentity, ...]:
        """Return seeded identities whose canonical identity remains unknown."""
        return tuple(
            identity
            for identity in self.snapshot_identities()
            if identity.root_pid in self.unknown_pids
            or (identity.starttime_ticks <= 0 and identity.fallback_create_time <= 0)
        )

    @property
    def root_identity_known(self) -> bool:
        """Whether the root has a canonical identity suitable for PID signaling."""
        identity = self.captured.get(self.root_pid, (0, 0.0))
        return (
            self.root_pid > 0
            and self.root_pid not in self.unknown_pids
            and (identity[0] > 0 or identity[1] > 0)
        )

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
                self.unknown_pids.discard(pid)
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
            retained_identities=self.snapshot_known_identities(),
            unknown_identities=self.snapshot_unknown_identities(),
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
    identity = ProcessIdentity(
        root_pid=pid,
        starttime_ticks=expected_starttime_ticks,
        fallback_create_time=expected_fallback_create_time,
    )
    return inspect_pid_identity(identity) is _IdentityStatus.ALIVE


def inspect_pid_identity(identity: ProcessIdentity) -> _IdentityStatus:
    """Classify one PID without conflating absence and identity uncertainty."""
    pid = identity.root_pid
    if pid <= 0:
        return _IdentityStatus.ABSENT
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return _IdentityStatus.ABSENT
    except psutil.AccessDenied:
        return _IdentityStatus.UNKNOWN
    return _inspect_process_identity(process, identity)


def _inspect_process_identity(
    process: psutil.Process,
    identity: ProcessIdentity,
) -> _IdentityStatus:
    """Validate ``identity`` against one retained psutil process handle."""
    if identity.starttime_ticks > 0:
        actual_ticks = read_starttime_ticks(identity.root_pid)
        if actual_ticks == identity.starttime_ticks:
            return _IdentityStatus.ALIVE
        if actual_ticks is not None:
            return _IdentityStatus.UNKNOWN
        try:
            return _IdentityStatus.UNKNOWN if process.is_running() else _IdentityStatus.ABSENT
        except psutil.NoSuchProcess:
            return _IdentityStatus.ABSENT
        except psutil.AccessDenied:
            return _IdentityStatus.UNKNOWN
    if identity.fallback_create_time <= 0:
        return _IdentityStatus.UNKNOWN
    try:
        with process.oneshot():
            create_time = process.create_time()
    except psutil.NoSuchProcess:
        return _IdentityStatus.ABSENT
    except psutil.AccessDenied:
        return _IdentityStatus.UNKNOWN
    if create_time == identity.fallback_create_time:
        return _IdentityStatus.ALIVE
    return _IdentityStatus.UNKNOWN


def signal_process_identity(
    identity: ProcessIdentity,
    signal_number: Signals,
) -> _IdentityStatus:
    """Signal exactly one process after an immediate canonical identity match."""
    try:
        process = psutil.Process(identity.root_pid)
    except psutil.NoSuchProcess:
        return _IdentityStatus.ABSENT
    except psutil.AccessDenied:
        return _IdentityStatus.UNKNOWN
    status = _inspect_process_identity(process, identity)
    if status is not _IdentityStatus.ALIVE:
        return status
    try:
        process.send_signal(signal_number)
    except psutil.NoSuchProcess:
        return _IdentityStatus.ABSENT
    except psutil.AccessDenied:
        return _IdentityStatus.UNKNOWN
    return _IdentityStatus.ALIVE


def time_remaining(deadline: float, *, now: float) -> float:
    """Return remaining time until ``deadline`` (monotonic clock)."""
    return max(0.0, deadline - now)
