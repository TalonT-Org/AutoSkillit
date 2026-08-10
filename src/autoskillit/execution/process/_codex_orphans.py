"""Orphaned interactive codex detection — fd 0 → deleted pty is the incident's observed signature.

Same-user scoped; reap is signal-only, so persisted ``~/.codex/sessions``
rollouts are never deleted.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psutil

from autoskillit.core import read_starttime_ticks

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

_PTS_PREFIX = "/dev/pts/"
_DELETED_SUFFIX = " (deleted)"


@dataclass(frozen=True, slots=True)
class OrphanedCodexProcess:
    """An interactive codex process whose fd 0 is a deleted pty."""

    pid: int
    fd0_target: str  # e.g. "/dev/pts/3 (deleted)"
    exe_target: str | None  # readlink /proc/<pid>/exe; operator display, never a gate
    starttime_ticks: int  # /proc/<pid>/stat field 22, identity anchor for reap
    started_at: float  # psutil create_time(), operator display


@dataclass(frozen=True, slots=True)
class CodexOrphanReapResult:
    """Per-orphan reap outcome."""

    pid: int
    action: Literal["terminated", "skipped", "incomplete"]
    survivor_pids: tuple[int, ...] = ()  # from ProcessCleanupResult
    access_denied_pids: tuple[int, ...] = ()  # from ProcessCleanupResult


# ---------------------------------------------------------------------------
# Predicate helpers
# ---------------------------------------------------------------------------


def _is_deleted_pty_target(target: str) -> bool:
    """True iff *target* is exactly ``/dev/pts/<digits> (deleted)``."""
    if not (target.startswith(_PTS_PREFIX) and target.endswith(_DELETED_SUFFIX)):
        return False
    return target[len(_PTS_PREFIX) : -len(_DELETED_SUFFIX)].isdigit()


def _fd0_deleted_pty_target(pid: int) -> str | None:
    """Return fd 0's link target iff it is a deleted pty, else ``None``."""
    try:
        target = os.readlink(f"/proc/{pid}/fd/0")
    except OSError:
        return None
    return target if _is_deleted_pty_target(target) else None


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def find_orphaned_codex_processes(
    *,
    process_name: str = "codex",
) -> list[OrphanedCodexProcess]:
    """Scan ``/proc`` for orphaned interactive codex processes.

    A process is flagged when its ``/proc/<pid>/comm`` equals *process_name*,
    it is owned by the invoking user, and its fd 0 resolves to a deleted pty
    (``/dev/pts/<digits> (deleted)``).

    This function never raises for ``/proc`` churn — per-PID ``OSError`` guards
    skip vanished or unreadable entries, preserving already-found orphans.
    """
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return []

    my_uid = os.geteuid()
    orphans: list[OrphanedCodexProcess] = []

    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        pid = int(name)

        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            continue
        if comm != process_name:
            continue

        # Same-user filter — destructive-targeting boundary.
        try:
            if os.stat(f"/proc/{pid}").st_uid != my_uid:
                continue
        except OSError:
            continue

        target = _fd0_deleted_pty_target(pid)
        if target is None:
            continue

        ticks = read_starttime_ticks(pid)
        if ticks is None:
            continue

        try:
            exe_target: str | None = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            exe_target = None

        try:
            started_at = psutil.Process(pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        orphans.append(
            OrphanedCodexProcess(
                pid=pid,
                fd0_target=target,
                exe_target=exe_target,
                starttime_ticks=ticks,
                started_at=started_at,
            )
        )

    return sorted(orphans, key=lambda o: o.pid)


# ---------------------------------------------------------------------------
# Reap
# ---------------------------------------------------------------------------


def reap_orphaned_codex_processes(
    orphans: Sequence[OrphanedCodexProcess],
) -> list[CodexOrphanReapResult]:
    """Terminate orphaned codex processes after re-verifying identity.

    Signal-only — no filesystem mutation anywhere in this module.
    """
    from ._process_kill import kill_process_tree

    results: list[CodexOrphanReapResult] = []
    for o in orphans:
        # Re-verify identity: starttime ticks must match AND fd 0 must still
        # be a deleted pty.  Any mismatch → skip (state-agnostic: the process
        # may have exited, been recycled, or changed its stdin).
        if (
            read_starttime_ticks(o.pid) != o.starttime_ticks
            or _fd0_deleted_pty_target(o.pid) is None
        ):
            results.append(CodexOrphanReapResult(o.pid, "skipped"))
            continue

        result = kill_process_tree(o.pid)
        if result.survivor_pids or result.access_denied_pids:
            results.append(
                CodexOrphanReapResult(
                    o.pid,
                    "incomplete",
                    survivor_pids=result.survivor_pids,
                    access_denied_pids=result.access_denied_pids,
                )
            )
        else:
            results.append(CodexOrphanReapResult(o.pid, "terminated"))

    return results
