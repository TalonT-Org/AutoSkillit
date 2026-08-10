"""Process tree kill utilities for subprocess lifecycle management."""

from __future__ import annotations

import os
import signal

import anyio
import anyio.abc
import psutil

from autoskillit.core import ProcessCleanupResult, get_logger

logger = get_logger(__name__)


def kill_process_tree(
    pid: int,
    timeout: float = 2.0,
    process_group_id: int | None = None,
) -> ProcessCleanupResult:
    """Kill a process and all its descendants. SIGTERM → wait → SIGKILL.

    Uses psutil to find ALL descendants (not just same process group),
    handles NoSuchProcess gracefully (PID already dead or recycled).

    Args:
        pid: PID of the root process to kill.
        timeout: Seconds to wait between SIGTERM and SIGKILL.
    """
    try:
        parent: psutil.Process | None = psutil.Process(pid)
    except psutil.NoSuchProcess:
        parent = None

    # Collect all children first (recursive)
    try:
        children = parent.children(recursive=True) if parent is not None else []
    except psutil.NoSuchProcess:
        children = []

    # Include the parent in the kill list
    all_procs = children + ([parent] if parent is not None else [])
    if process_group_id is not None:
        known = {proc.pid for proc in all_procs}
        for candidate in psutil.process_iter():
            try:
                if (
                    candidate.pid not in known
                    and candidate.pid != os.getpid()
                    and os.getpgid(candidate.pid) == process_group_id
                ):
                    all_procs.append(candidate)
                    known.add(candidate.pid)
            except (OSError, psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    identities: list[tuple[int, float]] = []
    access_denied: set[int] = set()
    for proc in all_procs:
        try:
            identities.append((proc.pid, proc.create_time()))
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            access_denied.add(proc.pid)

    # Send SIGTERM to all
    for proc in all_procs:
        try:
            proc.send_signal(signal.SIGTERM)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            access_denied.add(proc.pid)

    # Wait for graceful shutdown
    _, alive_after_term = psutil.wait_procs(all_procs, timeout=timeout)

    # SIGKILL survivors
    for proc in alive_after_term:
        try:
            proc.send_signal(signal.SIGKILL)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            access_denied.add(proc.pid)

    # Brief final wait produces authoritative local survivor evidence.
    _, alive_after_kill = psutil.wait_procs(alive_after_term, timeout=1.0)
    survivor_pids = tuple(sorted(proc.pid for proc in alive_after_kill))
    observed_pids = {observed_pid for observed_pid, _ in identities}
    terminated_pids = tuple(sorted(observed_pids - set(survivor_pids)))
    return ProcessCleanupResult(
        root_pid=pid,
        process_identities=tuple(sorted(identities)),
        terminated_pids=terminated_pids,
        survivor_pids=survivor_pids,
        access_denied_pids=tuple(sorted(access_denied)),
    )


async def async_kill_process_tree(
    pid: int,
    timeout: float = 2.0,
    process_group_id: int | None = None,
) -> ProcessCleanupResult:
    """Non-blocking wrapper around kill_process_tree for async callers."""
    return await anyio.to_thread.run_sync(kill_process_tree, pid, timeout, process_group_id)


async def _wait_process_dead(proc: psutil.Process, timeout: float = 5.0) -> bool:
    """Wait until proc is dead and its zombie is reaped. Returns True if dead within timeout.

    Uses psutil.Process.wait() rather than polling pid_exists():
    - For child processes: calls os.waitpid(), reaping the zombie. Only then is the PID
      truly gone from the process table.
    - For non-child processes (grandchildren adopted by init): psutil polls internally,
      which is equivalent to pid_exists() but still handles the NoSuchProcess case correctly.

    pid_exists() returns True for zombies (killed but not reaped), so wait() is required
    for reliable dead confirmation.
    """
    try:
        await anyio.to_thread.run_sync(proc.wait, timeout)
        return True
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        return True
