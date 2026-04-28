from __future__ import annotations

from autoskillit.core._linux_proc import read_boot_id, read_starttime_ticks
from autoskillit.fleet.state import DispatchRecord


def is_dispatch_session_alive(record: DispatchRecord) -> bool:
    """True only when boot_id, PID, and starttime_ticks all match — False on non-Linux."""
    if not record.l2_pid or not record.l2_boot_id:
        return False
    current_boot_id = read_boot_id()
    if current_boot_id is None or current_boot_id != record.l2_boot_id:
        return False
    actual_ticks = read_starttime_ticks(record.l2_pid)
    if actual_ticks is None:
        return False
    return actual_ticks == record.l2_starttime_ticks
