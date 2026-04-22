"""Minimal /proc filesystem readers for process identity.

Stdlib-only — no psutil, no autoskillit imports. Safe for L0 core.
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
        # comm may contain ")" — use rfind to find the *last* ")" as the boundary
        rpar = stat.rfind(")")
        if rpar == -1:
            return None
        fields = stat[rpar + 2 :].split()
        # starttime is field 22 (1-indexed per man page), offset 19 from the field after ")"
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        pass
    return None
