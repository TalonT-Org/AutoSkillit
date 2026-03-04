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

import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import anyio.abc
import psutil

if TYPE_CHECKING:
    from autoskillit.config import LinuxTracingConfig

LINUX_TRACING_AVAILABLE = sys.platform == "linux"


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


def read_proc_snapshot(pid: int) -> ProcSnapshot | None:
    """Read a complete snapshot for pid. Returns None if process gone."""
    if not LINUX_TRACING_AVAILABLE:
        return None
    captured_at = datetime.now(UTC).isoformat()
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            state = p.status()
            mem = p.memory_info()
            num_threads = p.num_threads()
            num_fds = p.num_fds()
            fd_soft_limit = p.rlimit(psutil.RLIMIT_NOFILE)[0]
            ctx = p.num_ctx_switches()
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
    )


async def proc_monitor(pid: int, interval: float = 5.0) -> AsyncIterator[ProcSnapshot]:
    """Async generator: yields ProcSnapshot at interval until process dies."""
    while True:
        snap = read_proc_snapshot(pid)
        if snap is None:
            return
        yield snap
        await anyio.sleep(interval)


@dataclass
class LinuxTracingHandle:
    """Opaque handle returned by start_linux_tracing. Call stop() when done."""

    _monitor_cancel_scope: anyio.CancelScope | None = None
    _snapshots: list[ProcSnapshot] = field(default_factory=list)

    async def stop(self) -> list[ProcSnapshot]:
        """Stop tracing and return accumulated snapshots."""
        if self._monitor_cancel_scope is not None:
            self._monitor_cancel_scope.cancel()
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

    async def _run_monitor() -> None:
        with scope:
            async for snap in proc_monitor(pid, config.proc_interval):
                handle._snapshots.append(snap)

    handle._monitor_cancel_scope = scope
    tg.start_soon(_run_monitor)

    return handle
