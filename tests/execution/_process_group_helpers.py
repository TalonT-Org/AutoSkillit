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


def _cleanup_process_identities(
    identities: Mapping[int, float],
    *,
    timeout: float = 1,
    poll_interval: float = 0.02,
) -> set[int]:
    """Terminate only PIDs whose captured creation identity still matches."""
    targets = dict(identities)
    for pid, create_time in targets.items():
        try:
            candidate = psutil.Process(pid)
            if candidate.create_time() == create_time:
                candidate.terminate()
        except (OSError, psutil.Error):
            continue

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _live_identities(targets):
            return set(targets)
        time.sleep(poll_interval)
    for pid in _live_identities(targets):
        try:
            candidate = psutil.Process(pid)
            if candidate.create_time() == targets[pid]:
                candidate.kill()
        except (OSError, psutil.Error):
            continue
    return set(targets)


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
    if leader_create_time is not None and _owned_group_anchor_is_valid(
        process, leader_create_time
    ):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass

    nonleader = {pid: created for pid, created in identities.items() if pid != process.pid}
    deadline = time.monotonic() + timeout
    while _live_identities(nonleader) and time.monotonic() < deadline:
        time.sleep(poll_interval)
    if _live_identities(nonleader):
        try:
            if leader_create_time is not None and _owned_group_anchor_is_valid(
                process, leader_create_time
            ):
                os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if leader_create_time is not None and _owned_group_anchor_is_valid(
                process, leader_create_time
            ):
                os.killpg(process.pid, signal.SIGKILL)
            else:
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
