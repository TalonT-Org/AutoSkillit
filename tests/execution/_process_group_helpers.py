"""Identity-fenced process cleanup helpers for execution tests."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Mapping

import psutil


def _capture_owned_group_identities(
    process: subprocess.Popen[object],
) -> dict[int, float]:
    """Capture group identities only while an unreaped leader anchors its PGID."""
    if process.returncode is not None or process.pid <= 0:
        return {}
    try:
        if os.getpgid(process.pid) != process.pid:
            return {}
    except OSError:
        return {}

    identities: dict[int, float] = {}
    for candidate in psutil.process_iter(["pid"]):
        try:
            if candidate.pid != os.getpid() and os.getpgid(candidate.pid) == process.pid:
                identities[candidate.pid] = candidate.create_time()
        except (OSError, psutil.Error):
            continue
    return identities


def _wait_for_identity_exit(
    identities: Mapping[int, float], *, timeout: float, poll_interval: float
) -> set[int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _live_identities(identities):
            return set()
        time.sleep(poll_interval)
    return _live_identities(identities)


def _cleanup_process_identities(
    identities: Mapping[int, float],
    *,
    timeout: float = 1,
    poll_interval: float = 0.02,
) -> set[int]:
    """Terminate matching identities and return any that remain live."""
    targets = dict(identities)
    for pid, create_time in targets.items():
        try:
            candidate = psutil.Process(pid)
            if candidate.create_time() == create_time:
                candidate.terminate()
        except (OSError, psutil.Error):
            continue

    live = _wait_for_identity_exit(targets, timeout=timeout, poll_interval=poll_interval)
    if not live:
        return set()
    for pid in live:
        try:
            candidate = psutil.Process(pid)
            if candidate.create_time() == targets[pid]:
                candidate.kill()
        except (OSError, psutil.Error):
            continue

    return _wait_for_identity_exit(targets, timeout=timeout, poll_interval=poll_interval)


def _owned_group_anchor_is_valid(
    process: subprocess.Popen[object],
    leader_create_time: float,
) -> bool:
    """Revalidate the unreaped leader identity immediately before group signaling."""
    if process.returncode is not None or process.pid <= 0:
        return False
    try:
        return (
            os.getpgid(process.pid) == process.pid
            and psutil.Process(process.pid).create_time() == leader_create_time
        )
    except (OSError, psutil.Error):
        return False


def _signal_owned_process_group(
    process: subprocess.Popen[object], leader_create_time: float | None, signum: signal.Signals
) -> bool:
    if leader_create_time is None or not _owned_group_anchor_is_valid(process, leader_create_time):
        return False
    os.killpg(process.pid, signum)
    return True


def _cleanup_owned_process_group(
    process: subprocess.Popen[object],
    *,
    timeout: float = 1,
    poll_interval: float = 0.02,
) -> set[int]:
    """Settle a directly spawned group before reaping its still-owned leader."""
    identities = _capture_owned_group_identities(process)
    if not identities:
        return set()
    leader_create_time = identities.get(process.pid)
    try:
        _signal_owned_process_group(process, leader_create_time, signal.SIGTERM)
    except OSError:
        pass

    nonleader = {pid: created for pid, created in identities.items() if pid != process.pid}
    if _wait_for_identity_exit(nonleader, timeout=timeout, poll_interval=poll_interval):
        try:
            _signal_owned_process_group(process, leader_create_time, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if not _signal_owned_process_group(process, leader_create_time, signal.SIGKILL):
                process.kill()
        except OSError:
            process.kill()
        process.wait(timeout=timeout)
    _cleanup_process_identities(nonleader, timeout=timeout, poll_interval=poll_interval)
    return set(identities)


def _live_identities(identities: Mapping[int, float]) -> set[int]:
    live: set[int] = set()
    for pid, create_time in identities.items():
        try:
            if psutil.Process(pid).create_time() == create_time:
                live.add(pid)
        except (OSError, psutil.Error):
            continue
    return live
