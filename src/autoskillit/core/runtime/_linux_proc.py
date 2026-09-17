"""Stdlib-only process-identity readers for supported local platforms.

Linux reads ``/proc``. Darwin obtains the boot timeval through ``sysctlbyname``
and process identity through ``proc_pidinfo``. The sampled identity is not a
process handle: callers must refuse an unavailable observation rather than
treating it as proof that a process is gone.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

_DEFAULT_PROC = Path("/proc")
_DARWIN_PROC_PIDTBSDINFO = 3
_DARWIN_ZOMBIE_STATUS = 5

__all__ = [
    "is_pid_alive",
    "is_pid_zombie",
    "is_session_alive",
    "owner_liveness",
    "read_boot_id",
    "read_pid_namespace_inode",
    "read_process_state",
    "read_starttime_ticks",
]


class _DarwinTimeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_int32)]


class _DarwinProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _darwin_boot_time() -> tuple[int, int] | None:
    """Return the Darwin boot timeval, refusing syscall or ABI failures."""
    boot_time = _DarwinTimeval()
    size = ctypes.c_size_t(ctypes.sizeof(boot_time))
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        sysctlbyname = libc.sysctlbyname
        sysctlbyname.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        sysctlbyname.restype = ctypes.c_int
        result = sysctlbyname(
            b"kern.boottime",
            ctypes.byref(boot_time),
            ctypes.byref(size),
            None,
            0,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if result != 0 or size.value != ctypes.sizeof(boot_time):
        return None
    if boot_time.tv_sec <= 0 or not 0 <= boot_time.tv_usec < 1_000_000:
        return None
    return boot_time.tv_sec, boot_time.tv_usec


def _read_darwin_proc_bsd_info(pid: int) -> _DarwinProcBsdInfo | None:
    """Return one exact Darwin process snapshot, or ``None`` on uncertainty."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    info = _DarwinProcBsdInfo()
    expected_size = ctypes.sizeof(info)
    if expected_size != 136:
        return None
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
        result = proc_pidinfo(
            pid,
            _DARWIN_PROC_PIDTBSDINFO,
            0,
            ctypes.byref(info),
            expected_size,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if result != expected_size or info.pbi_pid != pid:
        return None
    if info.pbi_start_tvsec <= 0 or not 0 <= info.pbi_start_tvusec < 1_000_000:
        return None
    return info


def _darwin_boot_id() -> str | None:
    boot_time = _darwin_boot_time()
    if boot_time is None:
        return None
    seconds, microseconds = boot_time
    return f"{seconds}:{microseconds:06d}"


def read_boot_id(*, proc_root: Path = _DEFAULT_PROC) -> str | None:
    """Read the supported platform's boot identity."""
    if sys.platform == "darwin":
        return _darwin_boot_id()
    if not sys.platform.startswith("linux"):
        return None
    try:
        return (proc_root / "sys" / "kernel" / "random" / "boot_id").read_text().strip()
    except OSError:
        return None


def read_starttime_ticks(pid: int, *, proc_root: Path = _DEFAULT_PROC) -> int | None:
    """Read the supported platform's process-start identity.

    Linux returns /proc starttime ticks. Darwin returns process-start
    microseconds, derived from one ``proc_pidinfo(PROC_PIDTBSDINFO)`` sample.
    """
    if sys.platform == "darwin":
        info = _read_darwin_proc_bsd_info(pid)
        if info is None:
            return None
        return info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec
    if not sys.platform.startswith("linux"):
        return None
    try:
        stat = (proc_root / str(pid) / "stat").read_text()
        rpar = stat.rfind(")")
        if rpar == -1:
            return None
        fields = stat[rpar + 2 :].split()
        # starttime is field 22 (1-indexed per man page), offset 19 from the field after ")"
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        pass
    return None


def read_process_state(pid: int, *, proc_root: Path = _DEFAULT_PROC) -> str | None:
    """Read process state character from the supported platform.

    Uses rfind(")") to correctly locate the field boundary even when the
    process comm contains a ")" character. Matches psutil's own _parse_stat_file()
    which uses rfind(b")") for the same reason.
    """
    if sys.platform == "darwin":
        info = _read_darwin_proc_bsd_info(pid)
        if info is None:
            return None
        return "Z" if info.pbi_status == _DARWIN_ZOMBIE_STATUS else "R"
    if not sys.platform.startswith("linux"):
        return None
    try:
        stat = (proc_root / str(pid) / "stat").read_text()
        rpar = stat.rfind(")")
        if rpar == -1:
            return None
        fields = stat[rpar + 2 :].split()
        # state is field 3 (1-indexed per man page), the first field after ")"
        return fields[0]
    except (OSError, ValueError, IndexError):
        pass
    return None


def is_pid_zombie(pid: int, *, proc_root: Path = _DEFAULT_PROC) -> bool:
    """True when pid is a zombie or in the transient dead-reaping window.

    Matches 'Z' (zombie) and 'X' (dead, transient reaping per proc_pid_stat(5),
    available since Linux 2.6.0). An 'X'-state process is briefly observable
    before the kernel reaps it; treating it as non-zombie here lets a caller
    race the reaper and miss cleanup. For liveness checks that exclude both
    states, prefer is_pid_alive.
    """
    state = read_process_state(pid, proc_root=proc_root)
    return state in ("Z", "X")


def is_pid_alive(pid: int, *, proc_root: Path = _DEFAULT_PROC) -> bool:
    """True when a supported platform observes a non-dead, non-zombie PID.

    Excludes 'Z' (zombie) and 'X' (dead, transient reaping window, per
    proc_pid_stat(5), available since Linux 2.6.0). A 'X'-state process is
    brief but real: a downstream caller that treats it as alive can race
    against the reaper and miss cleanup.
    """
    state = read_process_state(pid, proc_root=proc_root)
    return state is not None and state not in ("Z", "X")


def read_pid_namespace_inode(pid: int, *, proc_root: Path = _DEFAULT_PROC) -> int | None:
    """Read the inode of a process's PID namespace from <proc_root>/pid/ns/pid.

    Discriminates identity triples that are only unique within one PID
    namespace (containers share the host boot_id, and a bind-mounted home
    directory can share a tether directory across namespaces too). Returns
    None on any failure — callers must treat that as "no discriminator
    available", never as a mismatch.
    """
    try:
        return (proc_root / str(pid) / "ns" / "pid").stat().st_ino
    except OSError:
        return None


def is_session_alive(
    pid: int, boot_id: str, starttime_ticks: int, *, proc_root: Path = _DEFAULT_PROC
) -> bool:
    """True only when sampled identity proves the process is presently live."""
    return owner_liveness(pid, boot_id, starttime_ticks, proc_root=proc_root) is True


def owner_liveness(
    pid: int,
    boot_id: str,
    starttime_ticks: int,
    *,
    proc_root: Path = _DEFAULT_PROC,
) -> bool | None:
    """Classify a sampled owner identity as live, dead, or unavailable.

    The registry lock serializes sidecar mutations but does not freeze process
    state while this identity is observed, so unavailable evidence remains a
    conservative refusal rather than proof of death.
    """
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or not isinstance(boot_id, str)
        or not boot_id
        or isinstance(starttime_ticks, bool)
        or not isinstance(starttime_ticks, int)
        or starttime_ticks <= 0
    ):
        return None
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        return None

    current_boot_id = read_boot_id(proc_root=proc_root)
    if current_boot_id is None or not current_boot_id:
        return None
    if current_boot_id != boot_id:
        return False

    if sys.platform == "darwin":
        info = _read_darwin_proc_bsd_info(pid)
        if info is None:
            return _pid_absence_liveness(pid)
        actual_start = info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec
        if actual_start != starttime_ticks:
            return False
        return info.pbi_status != _DARWIN_ZOMBIE_STATUS

    actual_ticks = read_starttime_ticks(pid, proc_root=proc_root)
    if actual_ticks is None:
        return _pid_absence_liveness(pid)
    if actual_ticks != starttime_ticks:
        return False
    state = read_process_state(pid, proc_root=proc_root)
    if state is None:
        return _pid_absence_liveness(pid)
    return state not in ("Z", "X")


def _pid_absence_liveness(pid: int) -> bool | None:
    """Return False only when the kernel affirmatively says ``pid`` is absent."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None
    return None
