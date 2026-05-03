"""Process tree kill utilities for subprocess lifecycle management."""

from __future__ import annotations

import signal

import anyio
import anyio.abc
import psutil

from autoskillit.core import get_logger

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
