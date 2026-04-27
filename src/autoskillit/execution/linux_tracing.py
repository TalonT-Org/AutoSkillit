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
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING

import anyio
import anyio.abc
import psutil

from autoskillit.core import get_logger
from autoskillit.core import read_boot_id as read_boot_id
from autoskillit.core import read_starttime_ticks as read_starttime_ticks

if TYPE_CHECKING:
    from autoskillit.config import LinuxTracingConfig

logger = get_logger(__name__)

LINUX_TRACING_AVAILABLE = sys.platform == "linux"


# ---------------------------------------------------------------------------
# TraceTarget: workload process identity with provenance
# ---------------------------------------------------------------------------


class TraceTargetResolutionError(RuntimeError):
    """Raised when resolve_trace_target cannot find the expected workload within timeout.

    Attributes:
        root_pid: The spawn PID (e.g., script(1)) that was walked.
        expected_basename: The basename we were looking for (e.g., 'claude').
    """

    def __init__(self, root_pid: int, expected_basename: str) -> None:
        self.root_pid = root_pid
        self.expected_basename = expected_basename
        super().__init__(
            f"resolve_trace_target: timeout waiting for '{expected_basename}' "
            f"to appear as a descendant of PID {root_pid}. "
            f"The workload process did not start within the resolution window. "
            f"Cannot trace: falling back to wrapper PID would recreate issue #806."
        )


@dataclass(frozen=True)
class TraceTarget:
    """Workload process identity with provenance — the correct target for the tracer.

    Can only be produced by resolve_trace_target() (PTY mode, walks descendants)
    or trace_target_from_pid() (non-PTY mode, direct PID). Never from a raw int.

    Fields:
        pid: PID of the workload process (not the spawn wrapper).
        comm: /proc/{pid}/comm value — process name, max 15 chars.
        cmdline: Full command line as a tuple of strings.
        starttime_ticks: /proc/{pid}/stat field 22 — collision-resistant identity.
        resolved_at: UTC datetime when this target was resolved.
    """

    pid: int
    comm: str
    cmdline: tuple[str, ...]
    starttime_ticks: int
    resolved_at: datetime


async def resolve_trace_target(
    root_pid: int,
    expected_basename: str,
    timeout: float = 2.0,
) -> TraceTarget:
    """Walk descendants of root_pid to find the workload process by basename.

    Used after anyio.open_process() in PTY mode: root_pid is the script(1) wrapper;
    we need to find the actual workload (e.g., 'claude') in its subtree.

    Polls at 50 ms intervals up to timeout. Raises TraceTargetResolutionError on miss
    — never falls back to root_pid to prevent silent re-introduction of issue #806.

    Args:
        root_pid: Spawn PID (e.g., script(1) when PTY mode is active).
        expected_basename: Basename to match (e.g., 'claude', 'python3').
        timeout: Maximum seconds to wait for the workload to appear.

    Returns:
        TraceTarget with the workload's pid, comm, cmdline, starttime_ticks.

    Raises:
        TraceTargetResolutionError: When workload not found within timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            root_proc = psutil.Process(root_pid)
            children = root_proc.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            break

        for child in children:
            try:
                name = child.name()
                cmdline = child.cmdline()
                basename_matches = name == expected_basename or (
                    cmdline and Path(cmdline[0]).name == expected_basename
                )
                if not basename_matches:
                    continue
                # Found the workload — read identity fields from /proc
                try:
                    comm = Path(f"/proc/{child.pid}/comm").read_text().strip()
                except OSError:
                    comm = name
                starttime = read_starttime_ticks(child.pid) or 0
                return TraceTarget(
                    pid=child.pid,
                    comm=comm,
                    cmdline=tuple(cmdline),
                    starttime_ticks=starttime,
                    resolved_at=datetime.now(UTC),
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue

        await anyio.sleep(0.05)

    raise TraceTargetResolutionError(root_pid=root_pid, expected_basename=expected_basename)


def trace_target_from_pid(pid: int) -> TraceTarget:
    """Build a TraceTarget directly from a PID without child walking.

    Used for pty_mode=False (direct child, no wrapper) and in tests where the
    spawn PID is already the workload.

    Does a single /proc read to populate comm, cmdline, and starttime_ticks.
    Never raises — returns empty strings/tuples on /proc read errors (process
    may have already exited when this is called).
    """
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        comm = ""
    try:
        proc = psutil.Process(pid)
        cmdline: tuple[str, ...] = tuple(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        cmdline = ()
    starttime = read_starttime_ticks(pid) or 0
    return TraceTarget(
        pid=pid,
        comm=comm,
        cmdline=cmdline,
        starttime_ticks=starttime,
        resolved_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# TraceEnrollmentRecord: schema version 2 (adds comm field)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceEnrollmentRecord:
    """Identity triple written atomically at trace-open time.

    (boot_id, pid, starttime_ticks) together form a collision-resistant identity:
    - boot_id rejects pre-reboot stale files
    - starttime_ticks detects PID recycling

    Schema version 2 adds 'comm' to the enrollment record so that crash recovery
    can verify the process identity (not just its PID) and reject alien trace files.
    """

    schema_version: int  # 2 for post-#806 records; 1 for pre-fix records
    pid: int
    boot_id: str | None  # read_boot_id(); None if /proc unavailable
    starttime_ticks: int | None  # read_starttime_ticks(pid); None if unavailable
    session_id: str  # caller-provided; "" if not yet resolved
    enrolled_at: str  # ISO 8601 UTC
    kitchen_id: str  # ""
    order_id: str  # ""
    comm: str = ""  # /proc/{pid}/comm value; "" for pre-fix (schema_version=1) records


def _write_enrollment_atomic(path: Path, record: TraceEnrollmentRecord) -> None:
    """Write enrollment sidecar atomically using tempfile + os.replace."""
    content = json.dumps(dataclasses.asdict(record))
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_enrollment(path: Path) -> TraceEnrollmentRecord | None:
    """Read and validate an enrollment sidecar. Returns None on any error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return TraceEnrollmentRecord(
            schema_version=data["schema_version"],
            pid=data["pid"],
            boot_id=data.get("boot_id"),
            starttime_ticks=data.get("starttime_ticks"),
            session_id=data.get("session_id", ""),
            enrolled_at=data.get("enrolled_at", ""),
            kitchen_id=data.get("kitchen_id", ""),
            order_id=data.get("order_id", ""),
            comm=data.get("comm", ""),  # "" for pre-fix schema_version=1 records
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# ProcSnapshot: point-in-time snapshot with self-identifying comm field
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcSnapshot:
    """Point-in-time snapshot of process state.

    The 'comm' field (added in #806 fix) self-identifies the process each snapshot
    describes. Post-hoc drift detection becomes trivial: any row in proc_trace.jsonl
    where comm != expected_comm is a drift indicator.
    """

    # Temporal anchor — set at capture time, never reassigned
    captured_at: str
    # Process identity — populated from /proc/{pid}/comm (max 15 chars kernel truncation)
    comm: str
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
    # Best-effort /proc/{pid}/net/tcp fields (Linux only; None when unavailable)
    api_connection_established: int | None = None
    api_connection_states: dict[str, int] | None = None
    # Best-effort /proc/{pid}/io fields (Linux only; None when unavailable)
    io_read_bytes: int | None = None
    io_write_bytes: int | None = None


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


_TCP_STATE_NAMES: dict[str, str] = {
    "01": "ESTABLISHED",
    "02": "SYN_SENT",
    "03": "SYN_RECV",
    "04": "FIN_WAIT1",
    "05": "FIN_WAIT2",
    "06": "TIME_WAIT",
    "07": "CLOSE",
    "08": "CLOSE_WAIT",
    "09": "LAST_ACK",
    "0A": "LISTEN",
    "0B": "CLOSING",
}
_API_PORT_HEX: str = "01BB"  # port 443 in big-endian hex (Linux /proc/net/tcp format)


def _parse_net_tcp(content: str) -> dict[str, int]:
    """Parse /proc/{pid}/net/tcp content for connections to port 443.

    Returns a dict mapping TCP state name to connection count.
    Returns {} on empty or header-only content.
    Lines with fewer than 4 fields are skipped silently.
    """
    counts: dict[str, int] = {}
    for line in content.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 4:
            continue
        rem_addr = parts[2]
        state_hex = parts[3].upper()
        if ":" not in rem_addr:
            continue
        rem_port = rem_addr.split(":")[1].upper()
        if rem_port != _API_PORT_HEX:
            continue
        state_name = _TCP_STATE_NAMES.get(state_hex, state_hex)
        counts[state_name] = counts.get(state_name, 0) + 1
    return counts


def _parse_proc_io(content: str) -> tuple[int | None, int | None]:
    """Parse /proc/{pid}/io content for read_bytes and write_bytes.

    Returns (read_bytes, write_bytes). Either may be None if the field
    is absent or unparseable.
    """
    read_b: int | None = None
    write_b: int | None = None
    for line in content.splitlines():
        if line.startswith("read_bytes:"):
            try:
                read_b = int(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("write_bytes:"):
            try:
                write_b = int(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
    return read_b, write_b


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

    # Read process name from /proc/{pid}/comm (max 15 chars, kernel-truncated)
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        comm = ""

    # Hand-rolled /proc reads for fields psutil doesn't expose
    try:
        status_content = Path(f"/proc/{pid}/status").read_text()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    sig_fields = _parse_proc_status(status_content)

    try:
        oom = int(Path(f"/proc/{pid}/oom_score").read_text().strip())
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        oom = -1

    try:
        wchan = Path(f"/proc/{pid}/wchan").read_text().strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        wchan = ""

    # Best-effort: /proc/{pid}/net/tcp and tcp6 (Linux network namespace for this PID)
    _api_conn_states: dict[str, int] | None = None
    try:
        _tcp_content = Path(f"/proc/{pid}/net/tcp").read_text()
        _states = _parse_net_tcp(_tcp_content)
        try:
            _tcp6_content = Path(f"/proc/{pid}/net/tcp6").read_text()
            for k, v in _parse_net_tcp(_tcp6_content).items():
                _states[k] = _states.get(k, 0) + v
        except (FileNotFoundError, PermissionError, OSError):
            pass
        _api_conn_states = _states if _states else {}
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        pass

    _api_conns_established: int | None = None
    if _api_conn_states is not None:
        _api_conns_established = _api_conn_states.get("ESTABLISHED", 0)

    # Best-effort: /proc/{pid}/io
    _io_read: int | None = None
    _io_write: int | None = None
    try:
        _io_content = Path(f"/proc/{pid}/io").read_text()
        _io_read, _io_write = _parse_proc_io(_io_content)
    except (FileNotFoundError, PermissionError, OSError):
        pass

    return ProcSnapshot(
        captured_at=captured_at,
        comm=comm,
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
        api_connection_established=_api_conns_established,
        api_connection_states=_api_conn_states,
        io_read_bytes=_io_read,
        io_write_bytes=_io_write,
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
    _enrollment_path: Path | None = field(default=None)

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
        if self._trace_path is not None:
            # Intentional: stop() cleans up its own trace file. Crash-recovery only reads
            # files left behind by processes that never called stop() — so this is correct.
            self._trace_path.unlink(missing_ok=True)
            self._trace_path = None
        if self._enrollment_path is not None:
            self._enrollment_path.unlink(missing_ok=True)
            self._enrollment_path = None
        return list(self._snapshots)


def start_linux_tracing(
    target: TraceTarget,
    config: LinuxTracingConfig,
    tg: anyio.abc.TaskGroup | None,
    *,
    session_id: str = "",
    kitchen_id: str = "",
    order_id: str = "",
) -> LinuxTracingHandle | None:
    """Start Linux tracing if all gates pass. Returns handle or None.

    Args:
        target: TraceTarget produced by resolve_trace_target() (PTY mode) or
                trace_target_from_pid() (non-PTY mode). Never pass a raw int PID —
                use the appropriate resolver to get a TraceTarget first (ARCH-008).
    """
    if not isinstance(target, TraceTarget):
        raise TypeError(
            f"start_linux_tracing: 'target' must be a TraceTarget, "
            f"got {type(target).__name__!r}. "
            "Use resolve_trace_target() (PTY mode) or trace_target_from_pid() (non-PTY mode) "
            "instead of passing a raw int pid. (ARCH-008 / issue #806)"
        )
    if not LINUX_TRACING_AVAILABLE or not config.enabled:
        return None
    if tg is None:
        return None

    pid = target.pid
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

        # Write enrollment sidecar atomically for crash-recovery identity contract
        # Schema version 2: includes comm field for alien-file rejection in recovery
        enrollment_path = tmpfs / f"autoskillit_enrollment_{pid}.json"
        try:
            record = TraceEnrollmentRecord(
                schema_version=2,
                pid=pid,
                boot_id=read_boot_id(),
                starttime_ticks=target.starttime_ticks,
                session_id=session_id,
                enrolled_at=datetime.now(UTC).isoformat(),
                kitchen_id=kitchen_id,
                order_id=order_id,
                comm=target.comm,
            )
            _write_enrollment_atomic(enrollment_path, record)
            handle._enrollment_path = enrollment_path
        except OSError as e:
            logger.warning("Failed to write enrollment sidecar for pid %d: %s", pid, e)
            handle._enrollment_path = None

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
