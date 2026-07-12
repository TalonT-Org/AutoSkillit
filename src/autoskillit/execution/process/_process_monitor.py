"""Session and heartbeat monitor coroutines for subprocess output tracking."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import psutil

from autoskillit.core import (
    CandidateSighting,
    ChannelBStatus,
    ChildLifecycleSnapshot,
    CompletionCandidate,
    CompletionCandidateSource,
    LifecycleDecision,
    fast_loads,
    get_logger,
)
from autoskillit.execution.process._process_jsonl import (
    _jsonl_contains_marker,
    _jsonl_has_record_type,
)

if TYPE_CHECKING:
    from autoskillit.core import StreamParser

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SessionMonitorResult:
    """Result from _session_log_monitor with discovered session identity."""

    status: ChannelBStatus | None
    session_id: str  # Claude Code session ID from JSONL filename stem, or ""
    orphaned_tool_result: bool = False
    snapshot: ChildLifecycleSnapshot | None = None
    decision: LifecycleDecision = LifecycleDecision.CONTINUE
    eligible_candidate: CompletionCandidate | None = None
    eligible_source: CompletionCandidateSource | None = None
    sightings: tuple[CandidateSighting, ...] = ()


@dataclass(slots=True)
class _SessionLogTailState:
    """Mutable state for one persistent binary session-log tail."""

    path: Path
    session_id: str
    cursor: int
    carry: bytes
    observed_size: int
    last_change: float
    suppression_start: float | None = None
    last_record_type: str | None = None
    error_count: int = 0


@dataclass(frozen=True, slots=True)
class _ParsedSessionLogRecord:
    """One complete parsed record with its native binary provenance."""

    value: dict[str, Any]
    raw: bytes
    exclusive_byte_offset: int
    session_id: str


@dataclass(frozen=True, slots=True)
class _SessionLogScanComplete:
    """Barrier emitted after a scan, including a failed terminal drain."""

    cursor: int
    observed_size: int
    session_id: str
    changed: bool
    scan_succeeded: bool = True
    producer_stopped: bool = False
    incomplete_carry: bool = False


_SessionLogTailEvent = _ParsedSessionLogRecord | _SessionLogScanComplete


async def _heartbeat(
    stdout_path: Path,
    record_types: frozenset[str] = frozenset({"result"}),
    completion_marker: str = "",
    stream_parser: StreamParser | None = None,
    _poll_interval: float = 0.5,
    _on_poll: Callable[[], None] | None = None,
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
            for line in new_content.splitlines():
                event = stream_parser.parse_line(line)
                if event is not None and event.is_terminal:
                    if not completion_marker or event.has_marker:
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


class ProcessActivityTracker:
    """Per-invocation cache of ``psutil.Process`` handles keyed by PID.

    Sole purpose: own the per-process CPU baselines that
    ``cpu_percent(interval=0)`` requires for meaningful deltas. The first
    call on a given handle returns ``0.0``; subsequent calls return usage
    since the previous call. Owning the cache here (rather than as a
    module-level dict) keeps the activity tracker invocation-scoped so
    two concurrent runs cannot leak CPU baselines across each other.

    Distinct from ``OwnedProcessIdentityTracker``: the identity tracker
    captures root/descendant PID/start-time identities for cleanup
    finalization; this tracker only owns CPU baselines. The two
    invariants must never be conflated — process identity is durable,
    CPU baselines are ephemeral.
    """

    __slots__ = ("_handles",)

    def __init__(self) -> None:
        self._handles: dict[int, psutil.Process] = {}

    def has_active_children(self, pid: int) -> bool:
        """Return True if any descendant of ``pid`` exceeds the CPU activity threshold.

        Used by ``_session_log_monitor`` to suppress stale-kill when background
        Bash tasks are actively running despite LLM/API being idle. The first
        sighting of a PID primes its CPU baseline (returns 0.0); subsequent
        sightings return the delta since the previous call.
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
            cached = self._handles.get(child.pid)
            if cached is None:
                self._handles[child.pid] = child
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

        for stale_pid in list(self._handles.keys() - live_pids):
            self._handles.pop(stale_pid, None)

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


async def _discover_session_log_file(
    session_log_dir: Path,
    spawn_time: float,
    *,
    expected_session_id: str | None,
    poll_interval: float,
    timeout: float,
) -> tuple[Path | None, ChannelBStatus | None]:
    """Find the post-spawn JSONL using the legacy identity/recency rules."""
    phase1_start = time.monotonic()
    error_count = 0
    while True:
        if time.monotonic() - phase1_start >= timeout:
            logger.warning(
                "Session log file not found within phase1_timeout (%.1fs); treating as stale",
                timeout,
            )
            return None, ChannelBStatus.STALE
        await anyio.sleep(poll_interval)
        try:
            candidates: list[tuple[Path, float]] = []
            for path in session_log_dir.iterdir():
                if path.suffix != ".jsonl":
                    continue
                try:
                    ctime = path.stat().st_ctime
                except FileNotFoundError:
                    continue
                if ctime > spawn_time:
                    candidates.append((path, ctime))
            if not candidates:
                error_count = 0
                continue

            session_file = None
            if expected_session_id:
                session_file = next(
                    (path for path, _ in candidates if path.stem == expected_session_id),
                    None,
                )
                if session_file is None:
                    logger.warning(
                        "session_id_match_not_found",
                        expected_session_id=expected_session_id,
                        candidate_count=len(candidates),
                        candidate_stems=[path.stem for path, _ in candidates],
                    )
            if session_file is None:
                session_file, chosen_ctime = max(candidates, key=lambda candidate: candidate[1])
            else:
                chosen_ctime = next(ctime for path, ctime in candidates if path == session_file)

            logger.debug(
                "session_log_phase1_discovered",
                candidate_count=len(candidates),
                chosen_file=str(session_file),
                ctime=chosen_ctime,
                spawn_time=spawn_time,
                ctime_delta=chosen_ctime - spawn_time,
                selection_method=(
                    "session_id"
                    if expected_session_id and session_file.stem == expected_session_id
                    else "recency"
                ),
            )
            return session_file, None
        except FileNotFoundError:
            logger.warning("session_log_dir_absent", path=str(session_log_dir))
            return None, ChannelBStatus.DIR_MISSING
        except OSError:
            error_count += 1
            if error_count == 10:
                logger.warning(
                    "Session monitor: 10 consecutive failures reading %s",
                    session_log_dir,
                )


def _initialize_session_log_tail(path: Path) -> _SessionLogTailState:
    """Start a monitoring epoch at the file's current physical EOF."""
    try:
        initial_size = path.stat().st_size
    except OSError:
        logger.warning(
            "session_log_phase2_init_read_failed",
            file=str(path),
            fallback_scan_pos=0,
            exc_info=True,
        )
        initial_size = 0
    state = _SessionLogTailState(
        path=path,
        session_id=path.stem,
        cursor=initial_size,
        carry=b"",
        observed_size=initial_size,
        last_change=time.monotonic(),
    )
    logger.debug(
        "session_log_phase2_init",
        file=str(path),
        initial_scan_pos=state.cursor,
        initial_last_size=state.observed_size,
    )
    return state


def _scan_session_log(
    state: _SessionLogTailState,
    *,
    producer_stopped: bool,
) -> tuple[tuple[_ParsedSessionLogRecord, ...], _SessionLogScanComplete] | None:
    """Read and commit every newly available newline-terminated record."""
    try:
        current_size = state.path.stat().st_size
    except OSError:
        state.error_count += 1
        if state.error_count == 10:
            logger.warning(
                "Session monitor: 10 consecutive stat failures on %s",
                state.path,
            )
        return None

    read_offset = state.cursor + len(state.carry)
    reset = current_size < state.observed_size or current_size < read_offset
    changed = reset or current_size > state.observed_size
    if reset:
        state.cursor = 0
        state.carry = b""
        state.observed_size = current_size
        state.last_change = time.monotonic()
        state.suppression_start = None
        state.last_record_type = None
        read_offset = 0
    elif changed:
        state.observed_size = current_size
        state.last_change = time.monotonic()
        state.suppression_start = None

    try:
        with state.path.open("rb") as stream:
            stream.seek(read_offset)
            new_raw = stream.read()
    except OSError:
        state.error_count += 1
        if state.error_count == 10:
            logger.warning(
                "Session monitor: 10 consecutive read failures on %s",
                state.path,
            )
        return None
    state.error_count = 0

    merged = state.carry + new_raw
    last_newline = merged.rfind(b"\n")
    if last_newline < 0:
        state.carry = merged
        complete = b""
    else:
        complete = merged[: last_newline + 1]
        state.carry = merged[last_newline + 1 :]

    records: list[_ParsedSessionLogRecord] = []
    line_cursor = state.cursor
    if complete:
        for line_without_newline in complete[:-1].split(b"\n"):
            raw_line = line_without_newline + b"\n"
            line_cursor += len(raw_line)
            if not raw_line.strip():
                continue
            try:
                value = fast_loads(raw_line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(value, dict):
                continue
            record_type = value.get("type")
            if isinstance(record_type, str):
                state.last_record_type = record_type
            records.append(
                _ParsedSessionLogRecord(
                    value=value,
                    raw=raw_line,
                    exclusive_byte_offset=line_cursor,
                    session_id=state.session_id,
                )
            )
        state.cursor = line_cursor

    return tuple(records), _SessionLogScanComplete(
        cursor=state.cursor,
        observed_size=state.observed_size,
        session_id=state.session_id,
        changed=changed,
        producer_stopped=producer_stopped,
        incomplete_carry=bool(state.carry),
    )


async def _wait_for_tail_scan(
    poll_interval: float,
    producer_stop: anyio.Event | None,
) -> bool:
    """Wait for the next poll or a cooperative final-drain request."""
    if producer_stop is None:
        await anyio.sleep(poll_interval)
        return False
    if not producer_stop.is_set():
        with anyio.move_on_after(poll_interval):
            await producer_stop.wait()
    return producer_stop.is_set()


async def _tail_session_log_events(
    state: _SessionLogTailState,
    *,
    poll_interval: float = 2.0,
    producer_stop: anyio.Event | None = None,
    on_poll: Callable[[], None] | None = None,
) -> AsyncIterator[_SessionLogTailEvent]:
    """Yield parsed binary records and scan barriers until cooperatively stopped.

    A stop request triggers one final scan. Complete records from that drain are
    yielded before a final ``producer_stopped`` barrier; unresolved carry remains
    in *state* and is never decoded as a record. A failed final scan emits a
    barrier with ``scan_succeeded=False`` before the iterator terminates.
    """
    while True:
        stopping = await _wait_for_tail_scan(poll_interval, producer_stop)
        if on_poll is not None:
            on_poll()
        scanned = _scan_session_log(state, producer_stopped=stopping)
        if scanned is None:
            if stopping:
                yield _SessionLogScanComplete(
                    cursor=state.cursor,
                    observed_size=state.observed_size,
                    session_id=state.session_id,
                    changed=False,
                    scan_succeeded=False,
                    producer_stopped=True,
                    incomplete_carry=bool(state.carry),
                )
                return
            continue
        records, barrier = scanned
        for record in records:
            yield record
        yield barrier
        if stopping:
            return


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
    activity_tracker: ProcessActivityTracker | None = None,
    completion_record_callback: Callable[[dict[str, Any], int], bool] | None = None,
    eligible_source_on_completion: CompletionCandidateSource | None = None,
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

    ``completion_record_callback`` is invoked for a structurally matching record
    with that record's exclusive binary file offset. Returning ``False`` rejects
    the record and keeps the monitor running.
    """
    session_file, discovery_status = await _discover_session_log_file(
        session_log_dir,
        spawn_time,
        expected_session_id=expected_session_id,
        poll_interval=_phase1_poll,
        timeout=_phase1_timeout,
    )
    if session_file is None:
        assert discovery_status is not None
        return SessionMonitorResult(discovery_status, "")

    state = _initialize_session_log_tail(session_file)
    async for event in _tail_session_log_events(
        state,
        poll_interval=_phase2_poll,
        on_poll=_on_poll,
    ):
        if isinstance(event, _ParsedSessionLogRecord):
            if _jsonl_contains_marker(
                event.raw,
                completion_marker,
                record_types,
                base_byte_offset=event.exclusive_byte_offset - len(event.raw),
                completion_record_callback=completion_record_callback,
            ):
                logger.debug(
                    "session_log_phase2_marker_found",
                    file=str(state.path),
                    file_size=state.observed_size,
                    scan_pos=state.cursor,
                )
                return SessionMonitorResult(
                    ChannelBStatus.COMPLETION,
                    state.session_id,
                    decision=(
                        LifecycleDecision.ELIGIBLE
                        if eligible_source_on_completion is not None
                        else LifecycleDecision.CONTINUE
                    ),
                    eligible_source=eligible_source_on_completion,
                )
            continue

        if event.changed:
            continue
        elapsed = time.monotonic() - state.last_change
        if elapsed < stale_threshold:
            continue

        if pid is not None and _has_active_api_connection(pid):
            if state.suppression_start is None:
                state.suppression_start = time.monotonic()
            suppression_elapsed = time.monotonic() - state.suppression_start
            if suppression_elapsed >= max_suppression_seconds:
                logger.warning(
                    "Suppression bounded: stale kill after %.0fs consecutive "
                    "suppression (max_suppression_seconds=%.0f, pid=%d)",
                    suppression_elapsed,
                    max_suppression_seconds,
                    pid,
                )
                return SessionMonitorResult(ChannelBStatus.STALE, state.session_id)
            state.last_change = time.monotonic()
            logger.warning(
                "JSONL silent for %.0fs but ESTABLISHED port-443 connection — "
                "suppressing stale kill (pid=%d)",
                elapsed,
                pid,
            )
        elif (
            pid is not None
            and activity_tracker is not None
            and activity_tracker.has_active_children(pid)
        ):
            if state.suppression_start is None:
                state.suppression_start = time.monotonic()
            suppression_elapsed = time.monotonic() - state.suppression_start
            if suppression_elapsed >= max_suppression_seconds:
                logger.warning(
                    "Suppression bounded: stale kill after %.0fs consecutive "
                    "suppression (max_suppression_seconds=%.0f, pid=%d)",
                    suppression_elapsed,
                    max_suppression_seconds,
                    pid,
                )
                return SessionMonitorResult(ChannelBStatus.STALE, state.session_id)
            state.last_change = time.monotonic()
            logger.warning(
                "JSONL silent for %.0fs but child processes are CPU-active — "
                "suppressing stale kill (pid=%d)",
                elapsed,
                pid,
            )
        elif marker_dir is not None and _has_active_execution_marker(
            marker_dir, session_id=caller_session_id
        ):
            if state.suppression_start is None:
                state.suppression_start = time.monotonic()
            suppression_elapsed = time.monotonic() - state.suppression_start
            if suppression_elapsed >= max_suppression_seconds:
                logger.warning(
                    "Suppression bounded: stale kill after dispatch marker "
                    "suppression exceeded max_suppression_seconds",
                    suppression_elapsed=suppression_elapsed,
                    caller_session_id=caller_session_id,
                    marker_dir=str(marker_dir),
                )
                return SessionMonitorResult(ChannelBStatus.STALE, state.session_id)
            state.last_change = time.monotonic()
            logger.warning(
                "JSONL silent but active dispatch marker found — suppressing stale kill",
                stale_elapsed=elapsed,
                caller_session_id=caller_session_id,
                marker_dir=str(marker_dir),
            )
        else:
            return SessionMonitorResult(
                ChannelBStatus.STALE,
                state.session_id,
                orphaned_tool_result=(state.last_record_type == "user"),
            )

    raise RuntimeError("session log tail stopped without a producer_stop signal")
