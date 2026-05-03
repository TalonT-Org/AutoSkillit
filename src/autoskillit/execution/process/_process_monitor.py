"""Session and heartbeat monitor coroutines for subprocess output tracking."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import anyio
import psutil

from autoskillit.core import ChannelBStatus, get_logger
from autoskillit.execution.process._process_jsonl import (
    _jsonl_contains_marker,
    _jsonl_has_record_type,
    _jsonl_last_record_type,
)

logger = get_logger(__name__)


class SessionMonitorResult(NamedTuple):
    """Result from _session_log_monitor with discovered session identity."""

    status: ChannelBStatus
    session_id: str  # Claude Code session ID from JSONL filename stem, or ""
    orphaned_tool_result: bool = False


async def _heartbeat(
    stdout_path: Path,
    record_types: frozenset[str] = frozenset({"result"}),
    completion_marker: str = "",
    _poll_interval: float = 0.5,
    _on_poll: Callable[[], None] | None = None,
) -> str:
    """Poll session NDJSON output for a result-type record with non-empty content.

    Fires when a JSONL record whose ``"type"`` field is in *record_types* appears
    in stdout AND, for ``type=result`` records, the ``result`` field is non-empty.
    This guards against confirming on empty-result envelopes flushed before content
    is populated (drain-race false negative).

    When *completion_marker* is non-empty, ``type=result`` records additionally
    require the marker as a standalone line in the ``result`` field before Channel A
    fires — preventing premature confirmation on partial output.

    *_on_poll* is a test-only callback invoked after each sleep iteration. Pass
    ``None`` (the default) in production — zero overhead.
    """
    scan_pos = 0  # byte offset into the file
    os_error_count = 0
    while True:
        await anyio.sleep(_poll_interval)
        if _on_poll is not None:
            _on_poll()
        try:
            raw = stdout_path.read_bytes()
            os_error_count = 0
        except OSError:
            os_error_count += 1
            if os_error_count == 10:
                logger.warning("Heartbeat: 10 consecutive read failures on %s", stdout_path)
            continue
        new_raw = raw[scan_pos:]
        scan_pos = len(raw)
        new_content = new_raw.decode("utf-8", errors="replace")
        if _jsonl_has_record_type(new_content, record_types, completion_marker=completion_marker):
            return "completion"


def _has_active_api_connection(pid: int) -> bool:
    """Return True if the process tree rooted at `pid` has an ESTABLISHED TCP
    connection to port 443 (the Anthropic API endpoint).

    Used by _session_log_monitor to suppress stale-kill when a long-running
    API streaming call is in-flight.
    """
    try:
        parent = psutil.Process(pid)
        for proc in [parent] + parent.children(recursive=True):
            try:
                get_conns = getattr(proc, "net_connections", proc.connections)
                conns = get_conns(kind="tcp")
                for conn in conns:
                    if conn.status == "ESTABLISHED" and conn.raddr and conn.raddr.port == 443:
                        return True
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue
    except psutil.NoSuchProcess:
        pass
    return False


_CPU_ACTIVE_THRESHOLD: float = 10.0  # percent; evidence of actual computational work

# Cached Process objects keyed by PID so cpu_percent(interval=0) returns
# delta since the previous call on the *same* object rather than always 0.0
# on a freshly constructed psutil.Process.
_child_process_cache: dict[int, psutil.Process] = {}


def _has_active_child_processes(pid: int) -> bool:
    """Return True if any child process in the tree exceeds the CPU activity threshold.

    Used by _session_log_monitor to suppress stale-kill when background Bash tasks
    (launched via run_in_background: true) are actively running despite LLM/API being idle.

    cpu_percent(interval=0) returns usage since the last call per-process.  We
    cache psutil.Process objects across invocations so the second and subsequent
    calls on a given child produce meaningful CPU deltas (the first call on any
    new Process object always returns 0.0).
    """
    try:
        parent = psutil.Process(pid)
        current_children = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False

    live_pids: set[int] = set()
    active = False
    for child in current_children:
        live_pids.add(child.pid)
        cached = _child_process_cache.get(child.pid)
        if cached is None:
            # First sighting: prime cpu_percent baseline (returns 0.0).
            _child_process_cache[child.pid] = child
            try:
                child.cpu_percent(interval=0)
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                pass
            continue
        try:
            if cached.cpu_percent(interval=0) > _CPU_ACTIVE_THRESHOLD:
                active = True
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
            continue

    # Evict stale entries for children that no longer exist.
    for stale_pid in list(_child_process_cache.keys() - live_pids):
        _child_process_cache.pop(stale_pid, None)

    return active


async def _session_log_monitor(
    session_log_dir: Path,
    completion_marker: str,
    stale_threshold: float,
    spawn_time: float,
    record_types: frozenset[str] = frozenset({"assistant"}),
    pid: int | None = None,
    _phase1_poll: float = 1.0,
    _phase2_poll: float = 2.0,
    _phase1_timeout: float = 30.0,
    _on_poll: Callable[[], None] | None = None,
    expected_session_id: str | None = None,
    max_suppression_seconds: float = 1800.0,
) -> SessionMonitorResult:
    """Watch Claude Code session log for completion or staleness.

    Finds the session JSONL file (newest in session_log_dir created after
    spawn_time), then monitors it for:
    - completion_marker in a JSONL record of an allowed type -> return "completion"
    - No mtime change for stale_threshold seconds -> return "stale"

    The *record_types* parameter specifies which JSONL record types may
    contain the completion marker.  Defaults to ``{"assistant"}`` so that
    markers appearing in user prompts, queue-operation records, or tool
    results are ignored.

    *_phase1_timeout* caps how long Phase 1 may poll for a JSONL file.
    When no file appears within this window, returns "stale" immediately
    rather than spinning until the outer wall-clock timeout fires.

    *_on_poll* is a test-only callback invoked after each Phase 2 sleep iteration.
    Pass ``None`` (the default) in production — zero overhead.
    """
    import time as _time

    # Phase 1: Find the session log file
    session_file = None
    os_error_count = 0
    phase1_start = _time.monotonic()
    while session_file is None:
        if _time.monotonic() - phase1_start >= _phase1_timeout:
            logger.warning(
                "Session log file not found within phase1_timeout (%.1fs); treating as stale",
                _phase1_timeout,
            )
            return SessionMonitorResult(ChannelBStatus.STALE, "")
        await anyio.sleep(_phase1_poll)
        try:
            candidates = [
                f
                for f in session_log_dir.iterdir()
                if f.suffix == ".jsonl" and f.stat().st_ctime > spawn_time
            ]
            if candidates:
                if expected_session_id:
                    # Identity-based selection: match filename stem to session ID
                    for f in candidates:
                        if f.stem == expected_session_id:
                            session_file = f
                            break
                    if session_file is None:
                        logger.warning(
                            "session_id_match_not_found",
                            expected_session_id=expected_session_id,
                            candidate_count=len(candidates),
                            candidate_stems=[f.stem for f in candidates],
                        )
                        session_file = max(candidates, key=lambda f: f.stat().st_ctime)
                else:
                    session_file = max(candidates, key=lambda f: f.stat().st_ctime)
                _chosen_ctime = session_file.stat().st_ctime
                logger.debug(
                    "session_log_phase1_discovered",
                    candidate_count=len(candidates),
                    chosen_file=str(session_file),
                    ctime=_chosen_ctime,
                    spawn_time=spawn_time,
                    ctime_delta=_chosen_ctime - spawn_time,
                    selection_method="session_id"
                    if expected_session_id and session_file.stem == expected_session_id
                    else "recency",
                )
            os_error_count = 0
        except FileNotFoundError:
            # Directory missing is structural — it won't self-heal during a
            # poll loop.  Return immediately so downstream gates can
            # distinguish "could not monitor" from "monitored but timed out".
            logger.warning("session_log_dir_absent", path=str(session_log_dir))
            return SessionMonitorResult(ChannelBStatus.DIR_MISSING, "")
        except OSError:
            os_error_count += 1
            if os_error_count == 10:
                logger.warning(
                    "Session monitor: 10 consecutive failures reading %s", session_log_dir
                )
            continue

    # Extract session ID from the discovered JSONL filename stem
    _session_id = session_file.stem

    # Phase 2: Monitor the session log
    last_size = 0
    last_change = _time.monotonic()
    scan_pos = 0
    os_error_count = 0
    suppression_start_api: float | None = None
    suppression_start_child: float | None = None
    _last_record_type: str | None = None

    while True:
        await anyio.sleep(_phase2_poll)
        if _on_poll is not None:
            _on_poll()
        try:
            current_size = session_file.stat().st_size
            os_error_count = 0
        except OSError:
            os_error_count += 1
            if os_error_count == 10:
                logger.warning("Session monitor: 10 consecutive stat failures on %s", session_file)
            continue

        if current_size > last_size:
            last_size = current_size
            last_change = _time.monotonic()
            suppression_start_api = None
            suppression_start_child = None

            # Check new content for completion marker (structured)
            try:
                content = session_file.read_text(errors="replace")
                new_content = content[scan_pos:]
                scan_pos = len(content)
                if _jsonl_contains_marker(new_content, completion_marker, record_types):
                    logger.debug(
                        "session_log_phase2_marker_found",
                        file=str(session_file),
                        file_size=current_size,
                        scan_pos=scan_pos,
                    )
                    return SessionMonitorResult(ChannelBStatus.COMPLETION, _session_id)
                last_type_in_chunk = _jsonl_last_record_type(new_content)
                if last_type_in_chunk is not None:
                    _last_record_type = last_type_in_chunk
            except OSError:
                pass
        else:
            # Check staleness
            elapsed = _time.monotonic() - last_change
            if elapsed >= stale_threshold:
                if pid is not None and _has_active_api_connection(pid):
                    suppression_start_child = None
                    if suppression_start_api is None:
                        suppression_start_api = _time.monotonic()
                    if _time.monotonic() - suppression_start_api >= max_suppression_seconds:
                        logger.warning(
                            "Suppression bounded: stale kill after %.0fs consecutive "
                            "suppression (max_suppression_seconds=%.0f, pid=%d)",
                            _time.monotonic() - suppression_start_api,
                            max_suppression_seconds,
                            pid,
                        )
                        return SessionMonitorResult(ChannelBStatus.STALE, _session_id)
                    last_change = _time.monotonic()
                    logger.warning(
                        "JSONL silent for %.0fs but ESTABLISHED port-443 connection — "
                        "suppressing stale kill (pid=%d)",
                        elapsed,
                        pid,
                    )
                elif pid is not None and _has_active_child_processes(pid):
                    suppression_start_api = None
                    if suppression_start_child is None:
                        suppression_start_child = _time.monotonic()
                    if _time.monotonic() - suppression_start_child >= max_suppression_seconds:
                        logger.warning(
                            "Suppression bounded: stale kill after %.0fs consecutive "
                            "suppression (max_suppression_seconds=%.0f, pid=%d)",
                            _time.monotonic() - suppression_start_child,
                            max_suppression_seconds,
                            pid,
                        )
                        return SessionMonitorResult(ChannelBStatus.STALE, _session_id)
                    last_change = _time.monotonic()
                    logger.warning(
                        "JSONL silent for %.0fs but child processes are CPU-active — "
                        "suppressing stale kill (pid=%d)",
                        elapsed,
                        pid,
                    )
                else:
                    return SessionMonitorResult(
                        ChannelBStatus.STALE,
                        _session_id,
                        orphaned_tool_result=(_last_record_type == "user"),
                    )
