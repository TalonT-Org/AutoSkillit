"""Linux-only process tracing via psutil and /proc filesystem.

Accumulates ProcSnapshot objects in memory during the session, then flushes
them to structured JSON log files post-session (via session_log.py).

Gated behind:
- sys.platform == "linux"
- config.linux_tracing.enabled == True

Uses psutil (already a project dependency) for fields it handles well,
and hand-rolls /proc parsing only for fields psutil doesn't expose
(signal masks, oom_score, wchan).

On non-Linux platforms, all public functions are safe no-ops.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING

import anyio
import anyio.abc
import psutil

if TYPE_CHECKING:
    from autoskillit.config import LinuxTracingConfig

LINUX_TRACING_AVAILABLE = sys.platform == "linux"


def read_boot_id() -> str | None:
    """Read the system boot ID from /proc/sys/kernel/random/boot_id."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def read_starttime_ticks(pid: int) -> int | None:
    """Read process starttime ticks from /proc/pid/stat."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        # comm may contain spaces; find the closing paren to parse fields after it
        after_paren = stat.split(")", 1)
        if len(after_paren) >= 2:
            fields = after_paren[1].strip().split()
            # starttime is field 22 in /proc/stat (0-indexed position 19 after state)
            return int(fields[19])
    except (OSError, ValueError, IndexError):
        pass
    return None


@dataclass(frozen=True)
class ProcSnapshot:
    """Point-in-time snapshot of process state."""

    # Temporal anchor — set at capture time, never reassigned
    captured_at: str
    # psutil-sourced fields
    state: str
    vm_rss_kb: int
    threads: int
    fd_count: int
    fd_soft_limit: int
    ctx_switches_voluntary: int
    ctx_switches_involuntary: int
    # hand-rolled /proc fields (psutil doesn't expose these)
    sig_pnd: str
    sig_blk: str
    sig_cgt: str
    oom_score: int
    wchan: str
    # CPU utilisation (0.0 when process arg is not supplied to read_proc_snapshot)
    cpu_percent: float


def _parse_proc_status(content: str) -> dict[str, str]:
    """Parse /proc/pid/status for signal mask fields only.

    Only extracts SigPnd, SigBlk, SigCgt — all other fields come from psutil.
    """
    fields: dict[str, str] = {}
    for line in content.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "SigPnd":
            fields["sig_pnd"] = value
        elif key == "SigBlk":
            fields["sig_blk"] = value
        elif key == "SigCgt":
            fields["sig_cgt"] = value
    return fields


def read_proc_snapshot(pid: int, *, process: psutil.Process | None = None) -> ProcSnapshot | None:
    """Read a complete snapshot for pid. Returns None if process gone.

    When *process* is provided, it is reused instead of constructing a fresh
    psutil.Process(pid); cpu_percent(interval=0) then returns a meaningful
    delta against the baseline primed by the caller.  When *process* is None,
    cpu_percent defaults to 0.0.
    """
    if not LINUX_TRACING_AVAILABLE:
        return None
    captured_at = datetime.now(UTC).isoformat()
    try:
        p = process if process is not None else psutil.Process(pid)
        with p.oneshot():
            state = p.status()
            mem = p.memory_info()
            num_threads = p.num_threads()
            num_fds = p.num_fds()
            fd_soft_limit = p.rlimit(psutil.RLIMIT_NOFILE)[0]
            ctx = p.num_ctx_switches()
            cpu_pct = p.cpu_percent(interval=0) if process is not None else 0.0
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None

    # Hand-rolled /proc reads for fields psutil doesn't expose
    try:
        status_content = Path(f"/proc/{pid}/status").read_text()
    except (FileNotFoundError, PermissionError):
        return None
    sig_fields = _parse_proc_status(status_content)

    try:
        oom = int(Path(f"/proc/{pid}/oom_score").read_text().strip())
    except (FileNotFoundError, PermissionError, ValueError):
        oom = -1

    try:
        wchan = Path(f"/proc/{pid}/wchan").read_text().strip()
    except (FileNotFoundError, PermissionError):
        wchan = ""

    return ProcSnapshot(
        captured_at=captured_at,
        state=state,
        vm_rss_kb=mem.rss // 1024,
        threads=num_threads,
        fd_count=num_fds,
        fd_soft_limit=fd_soft_limit,
        ctx_switches_voluntary=ctx.voluntary,
        ctx_switches_involuntary=ctx.involuntary,
        sig_pnd=sig_fields.get("sig_pnd", ""),
        sig_blk=sig_fields.get("sig_blk", ""),
        sig_cgt=sig_fields.get("sig_cgt", ""),
        oom_score=oom,
        wchan=wchan,
        cpu_percent=cpu_pct,
    )


async def proc_monitor(pid: int, interval: float = 5.0) -> AsyncIterator[ProcSnapshot]:
    """Async generator: yields ProcSnapshot at interval until process dies.

    Guarantees that each yielded snapshot has a strictly increasing captured_at
    value. If the wall clock steps backward (e.g., NTP correction on WSL2),
    the captured_at is advanced by 1 microsecond from the previous value to
    maintain the monotonic ordering invariant at the production site.
    """
    _last_captured_at: str = ""
    try:
        _proc = psutil.Process(pid)
        _proc.cpu_percent(interval=0)  # prime the psutil-internal baseline
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return
    while True:
        snap = read_proc_snapshot(pid, process=_proc)
        if snap is None:
            return
        captured_at = snap.captured_at
        if captured_at <= _last_captured_at:
            # Clock stepped backward or stood still — advance by 1 microsecond
            captured_at = (
                datetime.fromisoformat(_last_captured_at) + timedelta(microseconds=1)
            ).isoformat()
            snap = dataclasses.replace(snap, captured_at=captured_at)
        _last_captured_at = captured_at
        yield snap
        await anyio.sleep(interval)


@dataclass
class LinuxTracingHandle:
    """Opaque handle returned by start_linux_tracing. Call stop() when done."""

    _monitor_cancel_scope: anyio.CancelScope | None = None
    _snapshots: list[ProcSnapshot] = field(default_factory=list)
    _trace_path: Path | None = field(default=None)
    _trace_file: IO[str] | None = field(default=None)

    def stop(self) -> list[ProcSnapshot]:
        """Stop tracing, flush and close the trace file, return accumulated snapshots."""
        if self._monitor_cancel_scope is not None:
            self._monitor_cancel_scope.cancel()
        if self._trace_file is not None:
            try:
                self._trace_file.flush()
                self._trace_file.close()
            except OSError:
                pass
            self._trace_file = None
        return list(self._snapshots)


def start_linux_tracing(
    pid: int,
    config: LinuxTracingConfig,
    tg: anyio.abc.TaskGroup | None,
) -> LinuxTracingHandle | None:
    """Start Linux tracing if all gates pass. Returns handle or None."""
    if not LINUX_TRACING_AVAILABLE or not config.enabled:
        return None
    if tg is None:
        return None

    handle = LinuxTracingHandle()
    scope = anyio.CancelScope()

    # Open tmpfs trace file for crash-resilient streaming (line-buffered)
    tmpfs = Path(config.tmpfs_path)
    if tmpfs.is_dir():
        trace_path = tmpfs / f"autoskillit_trace_{pid}.jsonl"
        try:
            handle._trace_path = trace_path
            handle._trace_file = trace_path.open("w", buffering=1)
        except OSError:
            handle._trace_path = None
            handle._trace_file = None

    async def _run_monitor() -> None:
        with scope:
            async for snap in proc_monitor(pid, config.proc_interval):
                handle._snapshots.append(snap)
                if handle._trace_file is not None:
                    try:
                        handle._trace_file.write(json.dumps(snap.__dict__) + "\n")
                    except OSError:
                        # Close broken file; degrade to in-memory only
                        try:
                            handle._trace_file.close()
                        except OSError:
                            pass
                        handle._trace_file = None

    handle._monitor_cancel_scope = scope
    tg.start_soon(_run_monitor)

    return handle
