"""Shared process-group cleanup helpers for execution tests."""

from __future__ import annotations

import os
import signal
import time

import psutil


def _process_group_members(process_group_id: int) -> set[int]:
    members: set[int] = set()
    for process in psutil.process_iter(["pid"]):
        try:
            if process.pid != os.getpid() and os.getpgid(process.pid) == process_group_id:
                members.add(process.pid)
        except (OSError, psutil.Error):
            continue
    return members


def _cleanup_process_group(
    process_group_id: int,
    *,
    timeout: float = 1,
    poll_interval: float = 0.02,
) -> set[int]:
    survivors = _process_group_members(process_group_id) if process_group_id else set()
    if not survivors:
        return survivors
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return survivors
    deadline = time.monotonic() + timeout
    while _process_group_members(process_group_id) and time.monotonic() < deadline:
        time.sleep(poll_interval)
    if _process_group_members(process_group_id):
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return survivors
