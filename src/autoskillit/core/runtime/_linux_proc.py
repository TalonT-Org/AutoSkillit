"""Minimal /proc filesystem readers for process identity.

Stdlib-only — no psutil, no autoskillit imports. Safe for IL-0 core.
On non-Linux platforms all functions return None.
"""

from __future__ import annotations

from pathlib import Path


def read_boot_id() -> str | None:
    """Read the system boot ID from /proc/sys/kernel/random/boot_id."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def read_starttime_ticks(pid: int) -> int | None:
    """Read process starttime ticks from /proc/pid/stat.

    Uses rfind(")") to correctly locate the field boundary even when the
    process comm contains a ")" character. Matches psutil's own _parse_stat_file()
    which uses rfind(b")") for the same reason.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        # comm may contain ")" — rfind locates the last ")" as the field boundary
        rpar = stat.rfind(")")
        if rpar == -1:
            return None
        fields = stat[rpar + 2 :].split()
        # starttime is field 22 (1-indexed per man page), offset 19 from the field after ")"
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        pass
    return None


def read_process_state(pid: int) -> str | None:
    """Read process state character from /proc/pid/stat.

    Uses rfind(")") to correctly locate the field boundary even when the
    process comm contains a ")" character. Matches psutil's own _parse_stat_file()
    which uses rfind(b")") for the same reason.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        # comm may contain ")" — rfind locates the last ")" as the field boundary
        rpar = stat.rfind(")")
        if rpar == -1:
            return None
        fields = stat[rpar + 2 :].split()
        # state is field 3 (1-indexed per man page), the first field after ")"
        return fields[0]
    except (OSError, ValueError, IndexError):
        pass
    return None


def is_pid_zombie(pid: int) -> bool:
    """True when pid is a zombie (exited but not yet reaped by its parent)."""
    return read_process_state(pid) == "Z"


def is_pid_alive(pid: int) -> bool:
    """True when pid exists and is not a dead or zombie state — False on non-Linux.

    Excludes both 'Z' (zombie) and 'X' (dead, in process of being reaped on
    Linux >= 2.6.33, per proc(5)). A 'X'-state process is brief but
    real: a downstream caller that treats it as alive can race against
    the reaper and miss cleanup.
    """
    state = read_process_state(pid)
    return state is not None and state not in ("Z", "X")


def is_session_alive(pid: int, boot_id: str, starttime_ticks: int) -> bool:
    """True only when boot_id, PID, and starttime_ticks all match — False on non-Linux."""
    if not pid or not boot_id:
        return False
    current_boot_id = read_boot_id()
    if current_boot_id is None or current_boot_id != boot_id:
        return False
    actual_ticks = read_starttime_ticks(pid)
    if actual_ticks is None:
        return False
    if actual_ticks != starttime_ticks:
        return False
    return is_pid_alive(pid)
