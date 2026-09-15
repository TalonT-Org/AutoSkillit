"""Identity-fenced non-owning process-tree recovery."""

from __future__ import annotations

import errno
import signal
import time
from collections.abc import Sequence
from functools import partial
from typing import Literal

import anyio
import psutil

from autoskillit.core import (
    ProcessCleanupResult,
    get_logger,
    read_boot_id,
    read_starttime_ticks,
)

logger = get_logger(__name__)

_FINAL_WAIT_SECONDS = 1.0


def _is_disappearance(exc: BaseException) -> bool:
    return isinstance(exc, (psutil.NoSuchProcess, ProcessLookupError)) or (
        isinstance(exc, OSError) and exc.errno == errno.ESRCH
    )


def _is_denial(exc: BaseException) -> bool:
    return isinstance(exc, (psutil.AccessDenied, PermissionError)) or (
        isinstance(exc, OSError) and exc.errno in {errno.EACCES, errno.EPERM}
    )


def _identity(proc: psutil.Process) -> tuple[int, float]:
    return proc.pid, proc.create_time()


def _signal_processes(
    processes: Sequence[psutil.Process], signum: signal.Signals, stage: Literal["term", "kill"]
) -> tuple[set[int], bool]:
    denied: set[int] = set()
    complete = True
    for proc in processes:
        try:
            proc.send_signal(signum)
        except (psutil.Error, OSError) as exc:
            if _is_disappearance(exc):
                continue
            complete = False
            if _is_denial(exc):
                denied.add(proc.pid)
            else:
                logger.warning(f"process_{stage}_failed", pid=proc.pid, exc_info=True)
    return denied, complete


def _wait_for_processes(
    processes: Sequence[psutil.Process],
    timeout: float,
    stage: Literal["term", "kill"],
    pid: int,
) -> tuple[list[psutil.Process], set[int], bool]:
    denied: set[int] = set()
    if timeout == 0:
        return list(processes), denied, True
    try:
        _, alive = psutil.wait_procs(processes, timeout=timeout)
        return alive, denied, True
    except psutil.TimeoutExpired as exc:
        if stage == "term":
            logger.debug("process_term_wait_timed_out", pid=getattr(exc, "pid", pid))
        return list(processes), denied, True
    except (psutil.Error, OSError) as exc:
        if _is_disappearance(exc):
            return [], denied, True
        if _is_denial(exc):
            denied.add(getattr(exc, "pid", pid))
        else:
            logger.warning(f"process_{stage}_wait_failed", pid=pid, exc_info=True)
        return list(processes), denied, False


def kill_process_tree(
    pid: int,
    timeout: float = 2.0,
    *,
    expected_boot_id: str | None = None,
    expected_starttime_ticks: int | None = None,
    expected_create_time: float | None = None,
    deadline: float | None = None,
) -> ProcessCleanupResult:
    """Observe and signal one positively identified PID tree, never a numeric PGID.

    This recovery primitive cannot reconstruct ownership.  A missing root is
    therefore incomplete evidence because its descendants cannot be enumerated.
    Expected disappearance is distinct from permission denial. Caller-supplied
    root identity is revalidated before descendants are observed or signaled.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    if timeout < 0:
        raise ValueError("timeout must be non-negative")

    def remaining_wait(maximum: float) -> float:
        if deadline is None:
            return maximum
        return min(maximum, max(0.0, deadline - time.monotonic()))

    if expected_boot_id is not None and read_boot_id() != expected_boot_id:
        return ProcessCleanupResult(root_pid=pid, identity_refused=True)

    if (
        expected_starttime_ticks is not None
        and read_starttime_ticks(pid) != expected_starttime_ticks
    ):
        return ProcessCleanupResult(root_pid=pid, identity_refused=True)

    denied: set[int] = set()
    complete = True
    try:
        parent = psutil.Process(pid)
    except (psutil.Error, OSError) as exc:
        if expected_create_time is not None:
            return ProcessCleanupResult(root_pid=pid, identity_refused=True)
        if _is_disappearance(exc):
            return ProcessCleanupResult(root_pid=pid, observation_complete=False)
        if _is_denial(exc):
            return ProcessCleanupResult(
                root_pid=pid,
                access_denied_pids=(pid,),
                observation_complete=False,
            )
        logger.warning("process_root_lookup_failed", pid=pid, exc_info=True)
        return ProcessCleanupResult(root_pid=pid, observation_complete=False)

    if expected_create_time is not None:
        try:
            actual_create_time = parent.create_time()
        except (psutil.Error, OSError):
            return ProcessCleanupResult(root_pid=pid, identity_refused=True)
        if actual_create_time != expected_create_time:
            return ProcessCleanupResult(root_pid=pid, identity_refused=True)

    try:
        children = parent.children(recursive=True)
    except (psutil.Error, OSError) as exc:
        children = []
        if _is_denial(exc):
            denied.add(pid)
        elif not _is_disappearance(exc):
            logger.warning("process_descendant_enumeration_failed", pid=pid, exc_info=True)
        complete = False

    all_procs = [*children, parent]
    identities: list[tuple[int, float]] = []
    signal_targets: list[psutil.Process] = []
    for proc in all_procs:
        try:
            identities.append(_identity(proc))
            signal_targets.append(proc)
        except (psutil.Error, OSError) as exc:
            if _is_disappearance(exc):
                continue
            if _is_denial(exc):
                denied.add(proc.pid)
            else:
                logger.warning("process_identity_capture_failed", pid=proc.pid, exc_info=True)
            complete = False

    term_denied, term_complete = _signal_processes(signal_targets, signal.SIGTERM, "term")
    alive_after_term, term_wait_denied, term_wait_complete = _wait_for_processes(
        signal_targets, remaining_wait(timeout), "term", pid
    )
    denied.update(term_denied, term_wait_denied)
    complete &= term_complete and term_wait_complete
    kill_denied, kill_complete = _signal_processes(alive_after_term, signal.SIGKILL, "kill")
    alive_after_kill, kill_wait_denied, kill_wait_complete = _wait_for_processes(
        alive_after_term, remaining_wait(_FINAL_WAIT_SECONDS), "kill", pid
    )
    denied.update(kill_denied, kill_wait_denied)
    complete &= kill_complete and kill_wait_complete
    survivor_pids = tuple(sorted(proc.pid for proc in alive_after_kill))
    observed_pids = {observed_pid for observed_pid, _ in identities}
    terminated_pids = tuple(sorted(observed_pids - set(survivor_pids)))
    return ProcessCleanupResult(
        root_pid=pid,
        process_identities=tuple(sorted(identities)),
        terminated_pids=terminated_pids,
        survivor_pids=survivor_pids,
        access_denied_pids=tuple(sorted(denied)),
        observation_complete=complete,
    )


async def async_kill_process_tree(
    pid: int,
    timeout: float = 2.0,
    *,
    expected_boot_id: str | None = None,
    expected_starttime_ticks: int | None = None,
    expected_create_time: float | None = None,
) -> ProcessCleanupResult:
    """Run identity-verified PID-tree cleanup without blocking the event loop."""
    cleanup = partial(
        kill_process_tree,
        pid,
        timeout,
        expected_boot_id=expected_boot_id,
        expected_starttime_ticks=expected_starttime_ticks,
        expected_create_time=expected_create_time,
    )
    return await anyio.to_thread.run_sync(cleanup)
