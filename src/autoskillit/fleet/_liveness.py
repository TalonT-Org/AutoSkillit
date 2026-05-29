from __future__ import annotations

import psutil

from autoskillit.core import read_boot_id, read_starttime_ticks
from autoskillit.fleet.state import DispatchRecord


def _create_time_fallback(pid: int, dispatched_create_time: float) -> bool:
    """Degraded identity confirmation via psutil create_time comparison."""
    try:
        if not psutil.pid_exists(pid):
            return False
        actual_ct = psutil.Process(pid).create_time()
        return abs(actual_ct - dispatched_create_time) < 1.0
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return False


def confirm_dispatch_identity(record: DispatchRecord) -> bool:
    """Confirm whether the process recorded in this dispatch is still running.

    Three-tier identity check:
    1. Full identity (pid + boot_id + ticks) — strict /proc match,
       with create_time fallback if /proc read fails
    2. Degraded identity (pid + create_time) — psutil create_time comparison
    3. No identity — False
    """
    if record.has_full_identity():
        current_boot_id = read_boot_id()
        if current_boot_id is None or current_boot_id != record.dispatched_boot_id:
            return False
        actual_ticks = read_starttime_ticks(record.dispatched_pid)
        if actual_ticks is not None:
            return actual_ticks == record.dispatched_starttime_ticks
        if record.dispatched_create_time > 0.0:
            return _create_time_fallback(record.dispatched_pid, record.dispatched_create_time)
        return False

    if record.has_degraded_identity():
        return _create_time_fallback(record.dispatched_pid, record.dispatched_create_time)

    return False


def is_dispatch_session_alive(record: DispatchRecord) -> bool:
    """True only when dispatch process identity is confirmed — False on non-Linux."""
    return confirm_dispatch_identity(record)
