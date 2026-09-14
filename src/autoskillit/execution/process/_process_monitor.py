"""Session and heartbeat monitor coroutines for subprocess output tracking."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

import anyio
import psutil

from autoskillit.core import VANISHED_ERRORS, ChannelBStatus, get_logger, scan_observed
from autoskillit.execution.process._process_jsonl import (
    EventCursor,
    _jsonl_contains_marker,
    _jsonl_has_record_type,
    _jsonl_last_record_type,
)

if TYPE_CHECKING:
    from autoskillit.core import ObservedEntry, SessionEvent, StreamParser

logger = get_logger(__name__)


class SessionMonitorResult(NamedTuple):
    """Result from _session_log_monitor with discovered session identity."""

    status: ChannelBStatus
    session_id: str  # Claude Code session ID from JSONL filename stem, or ""
    orphaned_tool_result: bool = False
    cursor: EventCursor | None = None


def _consume_heartbeat_events(
    new_content: str,
    stream_parser: StreamParser,
    completion_marker: str,
    on_event: Callable[[SessionEvent], None] | None,
) -> bool:
    """Parse new stdout records and report whether a terminal completion was seen."""
    for line in new_content.splitlines():
        event = stream_parser.parse_line(line)
        if event is not None and on_event is not None:
            on_event(event)
        if event is not None and event.is_terminal:
            if not completion_marker or event.has_marker:
                return True
    return False


async def _heartbeat(
    stdout_path: Path,
    record_types: frozenset[str] = frozenset({"result"}),
    completion_marker: str = "",
    stream_parser: StreamParser | None = None,
    _poll_interval: float = 0.5,
    _on_poll: Callable[[], None] | None = None,
    _on_event: Callable[[SessionEvent], None] | None = None,
) -> str:
    """Poll session NDJSON output for a result-type record with non-empty content.

    Fires when a JSONL record whose ``"type"`` field is in *record_types* appears
    in stdout AND, for ``type=result`` records, the ``result`` field is non-empty.
    This guards against confirming on empty-result envelopes flushed before content
    is populated (drain-race false negative).

    When *completion_marker* is non-empty, all matching record types additionally
    require the marker as a standalone line in their text content before Channel A
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
        if stream_parser is not None:
            if _consume_heartbeat_events(
                new_content,
                stream_parser,
                completion_marker,
                _on_event,
            ):
                return "completion"
        elif _jsonl_has_record_type(
            new_content, record_types, completion_marker=completion_marker
        ):
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


def _has_active_execution_marker(
    marker_dir: Path,
    session_id: str | None = None,
    max_marker_age: float = 60.0,
) -> bool:
    """Return True if any execution-in-progress marker was touched within max_marker_age secs."""
    try:
        now = time.time()
        pattern = (
            f"*-in-progress-{session_id}-*.marker"
            if session_id is not None
            else "*-in-progress-*.marker"
        )
        for p in marker_dir.glob(pattern):
            try:
                st = p.stat()
                if now - st.st_mtime <= max_marker_age:
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def _select_session_log_file(
    session_log_dir: Path,
    candidates: list[ObservedEntry],
    expected_session_id: str | None,
    resume_cursor: EventCursor | None,
) -> tuple[Path | None, ObservedEntry | None]:
    """Select a resumed or newly discovered session log from observed candidates."""
    if expected_session_id and resume_cursor is not None:
        expected_file = session_log_dir / f"{expected_session_id}.jsonl"
        if expected_file.is_file():
            return expected_file, None
    if not candidates:
        return None, None
    if expected_session_id:
        session_entry = next(
            (entry for entry in candidates if entry.path.stem == expected_session_id), None
        )
        if session_entry is None:
            logger.warning(
                "session_id_match_not_found",
                expected_session_id=expected_session_id,
                candidate_count=len(candidates),
                candidate_stems=[entry.path.stem for entry in candidates],
            )
            session_entry = max(candidates, key=lambda entry: entry.status.st_ctime)
    else:
        session_entry = max(candidates, key=lambda entry: entry.status.st_ctime)
    return session_entry.path, session_entry


async def _discover_session_log(
    session_log_dir: Path,
    spawn_time: float,
    phase1_poll: float,
    phase1_timeout: float,
    expected_session_id: str | None,
    resume_cursor: EventCursor | None,
) -> Path | SessionMonitorResult:
    """Find the session JSONL file or report why discovery cannot continue."""
    session_file = None
    os_error_count = 0
    phase1_start = time.monotonic()
    while session_file is None:
        if time.monotonic() - phase1_start >= phase1_timeout:
            logger.warning(
                "Session log file not found within phase1_timeout (%.1fs); treating as stale",
                phase1_timeout,
            )
            return SessionMonitorResult(ChannelBStatus.STALE, "")
        await anyio.sleep(phase1_poll)
        try:
            candidates = [
                entry
                for entry in scan_observed(session_log_dir)
                if entry.path.suffix == ".jsonl" and entry.status.st_ctime > spawn_time
            ]
            session_file, session_entry = _select_session_log_file(
                session_log_dir, candidates, expected_session_id, resume_cursor
            )
            if session_file is not None:
                chosen_ctime = session_entry.status.st_ctime if session_entry is not None else None
                logger.debug(
                    "session_log_phase1_discovered",
                    candidate_count=len(candidates),
                    chosen_file=str(session_file),
                    ctime=chosen_ctime,
                    spawn_time=spawn_time,
                    ctime_delta=chosen_ctime - spawn_time if chosen_ctime is not None else None,
                    selection_method="session_id"
                    if expected_session_id and session_file.stem == expected_session_id
                    else "recency",
                )
            os_error_count = 0
        except VANISHED_ERRORS:
            logger.warning("session_log_dir_absent", path=str(session_log_dir))
            return SessionMonitorResult(ChannelBStatus.DIR_MISSING, "")
        except OSError:
            os_error_count += 1
            if os_error_count == 10:
                logger.warning(
                    "Session monitor: 10 consecutive failures reading %s", session_log_dir
                )
    return session_file


StaleSuppressionReason = Literal[
    "pending_tasks", "api_connection", "child_processes", "dispatch_marker"
]


def _active_liveness_signals(
    pid: int | None,
    marker_dir: Path | None,
    session_id: str | None,
) -> frozenset[str]:
    """Return the subset of {'api_connection', 'child_processes', 'dispatch_marker'}
    currently active — the three liveness predicates shared with _termination.py's
    post-completion drain deferral.
    """
    signals: set[str] = set()
    if pid is not None and _has_active_api_connection(pid):
        signals.add("api_connection")
    if pid is not None and _has_active_child_processes(pid):
        signals.add("child_processes")
    if marker_dir is not None and _has_active_execution_marker(marker_dir, session_id=session_id):
        signals.add("dispatch_marker")
    return frozenset(signals)


def _stale_suppression_reason(
    has_pending_tasks: Callable[[], bool] | None,
    pid: int | None,
    marker_dir: Path | None,
    caller_session_id: str | None,
) -> StaleSuppressionReason | None:
    """Return the first active stale-suppression cause in priority order."""
    if has_pending_tasks is not None and has_pending_tasks():
        return "pending_tasks"
    active = _active_liveness_signals(pid, marker_dir, caller_session_id)
    if "api_connection" in active:
        return "api_connection"
    if "child_processes" in active:
        return "child_processes"
    if "dispatch_marker" in active:
        return "dispatch_marker"
    return None


def _continue_stale_suppression(
    reason: StaleSuppressionReason,
    suppression_start: float | None,
    max_suppression_seconds: float,
    elapsed: float,
    pid: int | None,
    marker_dir: Path | None,
    caller_session_id: str | None,
) -> tuple[float, float | None]:
    """Advance one active suppression window and log its cause-specific outcome."""
    if suppression_start is None:
        suppression_start = time.monotonic()
    if reason == "dispatch_marker":
        suppression_elapsed = time.monotonic() - suppression_start
        if suppression_elapsed >= max_suppression_seconds:
            logger.warning(
                "Suppression bounded: stale kill after dispatch marker "
                "suppression exceeded max_suppression_seconds",
                suppression_elapsed=suppression_elapsed,
                caller_session_id=caller_session_id,
                marker_dir=str(marker_dir),
            )
            return suppression_start, None
    elif time.monotonic() - suppression_start >= max_suppression_seconds:
        if reason != "pending_tasks":
            logger.warning(
                "Suppression bounded: stale kill after %.0fs consecutive "
                "suppression (max_suppression_seconds=%.0f, pid=%d)",
                time.monotonic() - suppression_start,
                max_suppression_seconds,
                pid,
            )
        return suppression_start, None
    last_change = time.monotonic()
    if reason == "api_connection":
        logger.warning(
            "JSONL silent for %.0fs but ESTABLISHED port-443 connection — "
            "suppressing stale kill (pid=%d)",
            elapsed,
            pid,
        )
    elif reason == "child_processes":
        logger.warning(
            "JSONL silent for %.0fs but child processes are CPU-active — "
            "suppressing stale kill (pid=%d)",
            elapsed,
            pid,
        )
    elif reason == "dispatch_marker":
        logger.warning(
            "JSONL silent but active dispatch marker found — suppressing stale kill",
            stale_elapsed=elapsed,
            caller_session_id=caller_session_id,
            marker_dir=str(marker_dir),
        )
    return suppression_start, last_change


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
    marker_dir: Path | None = None,
    caller_session_id: str | None = None,
    resume_cursor: EventCursor | None = None,
    on_session_file_selected: Callable[[Path, EventCursor], None] | None = None,
    has_pending_tasks: Callable[[], bool] | None = None,
) -> SessionMonitorResult:
    """Watch Claude Code session log for completion or staleness.

    Finds the session JSONL file (newest in session_log_dir created after
    spawn_time), then monitors it for:
    - completion_marker in a JSONL record of an allowed type -> return "completion"
    - No mtime change for stale_threshold seconds -> return "stale"

    The *record_types* parameter specifies which JSONL record types may
    contain the completion marker. Defaults to ``{"assistant"}`` so markers
    in user prompts, queue-operation records, or tool results are ignored.

    *_phase1_timeout* caps how long discovery may poll for a JSONL file.
    When no file appears within this window, returns "stale" immediately
    rather than spinning until the outer wall-clock timeout fires.

    *_on_poll* is a test-only callback invoked after each Phase 2 sleep iteration.
    Pass ``None`` (the default) in production.
    """
    discovered = await _discover_session_log(
        session_log_dir,
        spawn_time,
        _phase1_poll,
        _phase1_timeout,
        expected_session_id,
        resume_cursor,
    )
    if isinstance(discovered, SessionMonitorResult):
        return discovered
    session_file = discovered
    _session_id = session_file.stem
    lifecycle_cursor = (
        resume_cursor
        if resume_cursor is not None and resume_cursor.path == session_file
        else EventCursor(session_file)
    )
    if on_session_file_selected is not None:
        on_session_file_selected(session_file, lifecycle_cursor)
    # A resumed cursor starts at its run boundary; a fresh monitor starts
    # after existing content so earlier completion markers cannot win this run.
    try:
        scan_pos = (
            lifecycle_cursor.run_boundary
            if resume_cursor is not None
            else session_file.stat().st_size
        )
        last_size = session_file.stat().st_size
    except OSError:
        logger.warning(
            "session_log_phase2_init_read_failed",
            file=str(session_file),
            fallback_scan_pos=0,
            exc_info=True,
        )
        scan_pos = 0
        last_size = 0
    last_change = time.monotonic()
    logger.debug(
        "session_log_phase2_init",
        file=str(session_file),
        initial_scan_pos=scan_pos,
        initial_last_size=last_size,
    )
    os_error_count = 0
    suppression_start: float | None = None
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
            last_change = time.monotonic()
            suppression_start = None
            try:
                content = session_file.read_bytes()
                new_content = content[scan_pos:].decode("utf-8", errors="replace")
                scan_pos = len(content)
                if _jsonl_contains_marker(new_content, completion_marker, record_types):
                    logger.debug(
                        "session_log_phase2_marker_found",
                        file=str(session_file),
                        file_size=current_size,
                        scan_pos=scan_pos,
                    )
                    return SessionMonitorResult(
                        ChannelBStatus.COMPLETION,
                        _session_id,
                        cursor=lifecycle_cursor,
                    )
                last_type_in_chunk = _jsonl_last_record_type(new_content)
                if last_type_in_chunk is not None:
                    _last_record_type = last_type_in_chunk
            except OSError:
                pass
        else:
            elapsed = time.monotonic() - last_change
            if elapsed >= stale_threshold:
                reason = _stale_suppression_reason(
                    has_pending_tasks, pid, marker_dir, caller_session_id
                )
                if reason is None:
                    return SessionMonitorResult(
                        ChannelBStatus.STALE,
                        _session_id,
                        orphaned_tool_result=(_last_record_type == "user"),
                        cursor=lifecycle_cursor,
                    )
                suppression_start, next_last_change = _continue_stale_suppression(
                    reason,
                    suppression_start,
                    max_suppression_seconds,
                    elapsed,
                    pid,
                    marker_dir,
                    caller_session_id,
                )
                if next_last_change is None:
                    return SessionMonitorResult(
                        ChannelBStatus.STALE, _session_id, cursor=lifecycle_cursor
                    )
                last_change = next_last_change
