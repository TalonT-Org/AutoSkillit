"""Shared process-group cleanup helpers for execution tests."""

from __future__ import annotations

import os
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
    identities: dict[int, float] = {}
    for pid in survivors:
        try:
            identities[pid] = psutil.Process(pid).create_time()
        except (psutil.Error, OSError):
            continue
    for pid, create_time in identities.items():
        try:
            process = psutil.Process(pid)
            if process.create_time() == create_time:
                process.terminate()
        except (psutil.Error, OSError):
            continue
    deadline = time.monotonic() + timeout
    while _process_group_members(process_group_id) and time.monotonic() < deadline:
        time.sleep(poll_interval)
    remaining = _process_group_members(process_group_id)
    if remaining:
        for pid in remaining:
            create_time = identities.get(pid)
            if create_time is None:
                continue
            try:
                process = psutil.Process(pid)
                if process.create_time() == create_time:
                    process.kill()
            except (psutil.Error, OSError):
                continue
    return survivors
