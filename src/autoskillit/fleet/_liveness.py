from __future__ import annotations

from autoskillit.core import is_session_alive
from autoskillit.fleet.state import DispatchRecord


def is_dispatch_session_alive(record: DispatchRecord) -> bool:
    """True only when boot_id, PID, and starttime_ticks all match — False on non-Linux."""
    return is_session_alive(
        record.dispatched_pid, record.dispatched_boot_id, record.dispatched_starttime_ticks
    )
