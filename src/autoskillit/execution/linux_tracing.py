"""Linux-only process tracing via psutil and /proc filesystem.

Tier 2 debug instrumentation. Gated behind:
- sys.platform == "linux"
- config.linux_tracing.enabled == True
- logging level == DEBUG

Uses psutil (already a project dependency) for fields it handles well,
and hand-rolls /proc parsing only for fields psutil doesn't expose
(signal masks, oom_score, wchan).

On non-Linux platforms, all public functions are safe no-ops.
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import anyio.abc
import psutil

from autoskillit.core import get_logger

if TYPE_CHECKING:
    from autoskillit.config import LinuxTracingConfig

logger = get_logger(__name__)

LINUX_TRACING_AVAILABLE = sys.platform == "linux"


@dataclass(frozen=True)
class ProcSnapshot:
    """Point-in-time snapshot of process state."""

    # psutil-sourced fields
    state: str
    vm_rss_kb: int
    threads: int
    fd_count: int
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
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            state = p.status()
            mem = p.memory_info()
            num_threads = p.num_threads()
            num_fds = p.num_fds()
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
        state=state,
        vm_rss_kb=mem.rss // 1024,
        threads=num_threads,
        fd_count=num_fds,
        ctx_switches_voluntary=ctx.voluntary,
        ctx_switches_involuntary=ctx.involuntary,
        sig_pnd=sig_fields.get("sig_pnd", ""),
        sig_blk=sig_fields.get("sig_blk", ""),
        sig_cgt=sig_fields.get("sig_cgt", ""),
        oom_score=oom,
        wchan=wchan,
    )


def log_snapshot_delta(prev: ProcSnapshot, curr: ProcSnapshot, pid: int) -> None:
    """Log only fields that changed between consecutive snapshots."""
    changes: dict[str, dict[str, object]] = {}
    for f in dataclasses.fields(ProcSnapshot):
        old_val = getattr(prev, f.name)
        new_val = getattr(curr, f.name)
        if old_val != new_val:
            changes[f.name] = {"from": old_val, "to": new_val}
    if changes:
        logger.debug("proc_snapshot_delta", pid=pid, changes=changes)


async def proc_monitor(pid: int, interval: float = 5.0) -> AsyncIterator[ProcSnapshot]:
    """Async generator: yields ProcSnapshot at interval until process dies."""
    prev: ProcSnapshot | None = None
    while True:
        snap = read_proc_snapshot(pid)
        if snap is None:
            logger.debug("proc_monitor_target_gone", pid=pid)
            return
        if prev is not None:
            log_snapshot_delta(prev, snap, pid)
        else:
            logger.debug("proc_monitor_initial_snapshot", pid=pid, **dataclasses.asdict(snap))
        yield snap
        prev = snap
        await anyio.sleep(interval)


@dataclass
class LinuxTracingHandle:
    """Opaque handle returned by start_linux_tracing. Call stop() when done."""

    _monitor_cancel_scope: anyio.CancelScope | None = None

    async def stop(self) -> None:
        """Stop tracing. Best-effort, never raises."""
        if self._monitor_cancel_scope is not None:
            self._monitor_cancel_scope.cancel()


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
            async for _snap in proc_monitor(pid, config.proc_interval):
                pass  # Snapshots are logged by proc_monitor itself

    handle._monitor_cancel_scope = scope
    tg.start_soon(_run_monitor)

    return handle
