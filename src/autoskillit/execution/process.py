"""Subprocess lifecycle utilities providing pipe-blocking immunity.

Shared building blocks for all subprocess-spawning code in the project.
Uses temp file I/O (not pipes) to eliminate FD-inheritance blocking, and
psutil-based process tree cleanup with SIGTERM→SIGKILL escalation.

Two composed functions wire the utilities together correctly:
- ``run_managed_async`` for async callers
- ``run_managed_sync`` for sync callers
"""

from __future__ import annotations

import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import anyio
import anyio.abc
import psutil

from autoskillit.core import ChannelConfirmation, SubprocessResult, TerminationReason, get_logger

logger = get_logger(__name__)


def kill_process_tree(pid: int, timeout: float = 2.0) -> None:
    """Kill a process and all its descendants. SIGTERM → wait → SIGKILL.

    Uses psutil to find ALL descendants (not just same process group),
    handles NoSuchProcess gracefully (PID already dead or recycled).

    Args:
        pid: PID of the root process to kill.
        timeout: Seconds to wait between SIGTERM and SIGKILL.
    """
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    # Collect all children first (recursive)
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []

    # Include the parent in the kill list
    all_procs = children + [parent]

    # Send SIGTERM to all
    for proc in all_procs:
        try:
            proc.send_signal(signal.SIGTERM)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Wait for graceful shutdown
    _, alive = psutil.wait_procs(all_procs, timeout=timeout)

    # SIGKILL survivors
    for proc in alive:
        try:
            proc.send_signal(signal.SIGKILL)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Brief wait for kernel cleanup
    psutil.wait_procs(alive, timeout=1.0)


async def async_kill_process_tree(pid: int, timeout: float = 2.0) -> None:
    """Non-blocking wrapper around kill_process_tree for async callers."""
    await anyio.to_thread.run_sync(kill_process_tree, pid, timeout)


async def _wait_process_dead(proc: psutil.Process, timeout: float = 5.0) -> bool:
    """Wait until proc is dead and its zombie is reaped. Returns True if dead within timeout.

    Uses psutil.Process.wait() rather than polling pid_exists():
    - For child processes: calls os.waitpid(), reaping the zombie. Only then is the PID
      truly gone from the process table.
    - For non-child processes (grandchildren adopted by init): psutil polls internally,
      which is equivalent to pid_exists() but still handles the NoSuchProcess case correctly.

    pid_exists() returns True for zombies (killed but not reaped), so wait() is required
    for reliable dead confirmation.
    """
    try:
        await anyio.to_thread.run_sync(proc.wait, timeout)
        return True
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        return True


def pty_wrap_command(cmd: list[str]) -> list[str]:
    """Wrap a command with ``script`` to provide a PTY.

    Returns the original command unchanged if ``script`` is not available.

    Uses platform-appropriate flags:
    - Linux (GNU util-linux): ``script -qefc "<cmd>" /dev/null``
    - macOS (BSD script): ``script -q /dev/null command [args...]``
    """
    script_path = shutil.which("script")
    if script_path is None:
        return cmd
    if sys.platform == "darwin":
        # BSD script: transcript file precedes the command; args passed directly
        return [script_path, "-q", "/dev/null"] + cmd
    # GNU script: -e propagates exit code, -f flushes, -c accepts a shell string
    escaped = " ".join(shlex.quote(c) for c in cmd)
    return [script_path, "-qefc", escaped, "/dev/null"]


def _marker_is_standalone(text: str, marker: str) -> bool:
    """Check if the marker appears as a standalone line, not embedded in prose."""
    for text_line in text.splitlines():
        if text_line.strip() == marker:
            return True
    return False


def _jsonl_contains_marker(
    content: str,
    marker: str,
    record_types: frozenset[str],
) -> bool:
    """Check if any JSONL record of an allowed type contains the marker.

    Parses each line as JSON and extracts the content field based on the
    record type — ``message.content`` for assistant records, ``result`` for
    result records. The marker must appear as a standalone line within the
    extracted text, not embedded in surrounding prose.

    This prevents false-fires when the model quotes the marker directive
    in discussion (e.g. ``"I will emit %%AUTOSKILLIT_COMPLETE%% when done"``).
    """
    import json as _json

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except (ValueError, _json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        record_type = obj.get("type")
        if record_type not in record_types:
            continue

        if record_type == "assistant":
            raw = (obj.get("message") or {}).get("content", "")
        elif record_type == "result":
            raw = obj.get("result", "")
        else:
            raw = " ".join(v for v in obj.values() if isinstance(v, str))

        if isinstance(raw, list):
            text = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
        elif not isinstance(raw, str):
            text = "" if raw is None else str(raw)
        else:
            text = raw
        if _marker_is_standalone(text, marker):
            return True
    return False


def _jsonl_has_record_type(content: str, record_types: frozenset[str]) -> bool:
    """Check if any JSONL record of an allowed type exists in content.

    Used by the heartbeat to detect when Claude CLI emits a result record
    to stdout. For ``type=result`` records, additionally requires the ``result``
    field to be a non-empty string — confirming on an empty-result envelope
    is the source of the drain-race false negative.
    """
    import json as _json

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except (ValueError, _json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        record_type = obj.get("type")
        if record_type not in record_types:
            continue
        if record_type == "result":
            result_field = obj.get("result", "")
            if not (isinstance(result_field, str) and result_field.strip()):
                continue  # result absent, null, or empty — do not confirm
        return True
    return False


async def _heartbeat(
    stdout_path: Path,
    marker: str,
    record_types: frozenset[str] = frozenset({"result"}),
    _poll_interval: float = 0.5,
    _on_poll: Callable[[], None] | None = None,
) -> str:
    """Poll session NDJSON output for a result-type record with non-empty content.

    Fires when a JSONL record whose ``"type"`` field is in *record_types* appears
    in stdout AND, for ``type=result`` records, the ``result`` field is non-empty.
    This guards against confirming on empty-result envelopes flushed before content
    is populated (drain-race false negative). The *marker* parameter is accepted
    for API compatibility but is not used.

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
        if _jsonl_has_record_type(new_content, record_types):
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
) -> str:
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
            return "stale"
        await anyio.sleep(_phase1_poll)
        try:
            candidates = [
                f
                for f in session_log_dir.iterdir()
                if f.suffix == ".jsonl" and f.stat().st_ctime > spawn_time
            ]
            if candidates:
                session_file = max(candidates, key=lambda f: f.stat().st_ctime)
                _chosen_ctime = session_file.stat().st_ctime
                logger.debug(
                    "session_log_phase1_discovered",
                    candidate_count=len(candidates),
                    chosen_file=str(session_file),
                    ctime=_chosen_ctime,
                    spawn_time=spawn_time,
                    ctime_delta=_chosen_ctime - spawn_time,
                )
            os_error_count = 0
        except OSError:
            os_error_count += 1
            if os_error_count == 10:
                logger.warning(
                    "Session monitor: 10 consecutive failures reading %s", session_log_dir
                )
            continue

    # Phase 2: Monitor the session log
    last_size = 0
    last_change = _time.monotonic()
    scan_pos = 0
    os_error_count = 0

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
                    return "completion"
            except OSError:
                pass
        else:
            # Check staleness
            elapsed = _time.monotonic() - last_change
            if elapsed >= stale_threshold:
                if pid is not None and _has_active_api_connection(pid):
                    last_change = _time.monotonic()
                    logger.warning(
                        "JSONL silent for %.0fs but ESTABLISHED port-443 connection — "
                        "suppressing stale kill (pid=%d)",
                        elapsed,
                        pid,
                    )
                else:
                    return "stale"


@contextmanager
def create_temp_io(
    input_data: str | None = None,
) -> Generator[tuple[IO[bytes], IO[bytes], Path | None], None, None]:
    """Context manager yielding temp file paths for subprocess I/O.

    Creates temp files for stdout and stderr (and optionally stdin).
    Cleans up on exit regardless of success/failure.

    Yields:
        Tuple of (stdout_file, stderr_file, stdin_path_or_None) where
        stdout_file and stderr_file are open file handles ready to pass
        to subprocess, and stdin_path is a Path if input_data was provided.
    """
    stdout_file: IO[bytes] | None = None
    stderr_file: IO[bytes] | None = None
    stdin_path: Path | None = None
    paths_to_clean: list[Path] = []

    try:
        stdout_file = tempfile.NamedTemporaryFile(
            mode="w+b", prefix="proc_stdout_", suffix=".tmp", delete=False
        )
        paths_to_clean.append(Path(stdout_file.name))

        stderr_file = tempfile.NamedTemporaryFile(
            mode="w+b", prefix="proc_stderr_", suffix=".tmp", delete=False
        )
        paths_to_clean.append(Path(stderr_file.name))

        if input_data is not None:
            stdin_file = tempfile.NamedTemporaryFile(
                mode="w", prefix="proc_stdin_", suffix=".tmp", delete=False
            )
            stdin_file.write(input_data)
            stdin_file.flush()
            stdin_file.close()
            stdin_path = Path(stdin_file.name)
            paths_to_clean.append(stdin_path)

        yield stdout_file, stderr_file, stdin_path

    finally:
        # Close file handles if still open
        for f in (stdout_file, stderr_file):
            if f is not None:
                try:
                    f.close()
                except OSError:
                    pass

        # Delete temp files
        for p in paths_to_clean:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def read_temp_output(stdout_path: Path, stderr_path: Path) -> tuple[str, str]:
    """Read stdout/stderr from temp files. Safe even if children hold FDs.

    Files aren't EOF-gated like pipes, so this works regardless of whether
    child processes still have the file descriptors open.
    """
    stdout = ""
    stderr = ""
    try:
        stdout = stdout_path.read_text(errors="replace")
    except OSError:
        logger.warning("Failed to read stdout temp file: %s", stdout_path)
    try:
        stderr = stderr_path.read_text(errors="replace")
    except OSError:
        logger.warning("Failed to read stderr temp file: %s", stderr_path)
    return stdout, stderr


@dataclass(frozen=True)
class RaceSignals:
    """Accumulated signals produced by the anyio task group race in run_managed_async.

    Captures what happened without making any decisions about what it means.
    All fields are independent: multiple can be True simultaneously when tasks
    complete in the same event loop tick.
    """

    process_exited: bool
    process_returncode: int | None
    channel_a_confirmed: bool
    channel_b_status: str | None  # "completion", "stale", or None


@dataclass
class RaceAccumulator:
    """Mutable signal accumulator for the anyio task group race in run_managed_async.

    Each watch coroutine writes exactly one field before setting the trigger.
    In async cooperative concurrency there are no concurrent writes — only one
    coroutine runs between yield points. to_race_signals() converts to the
    frozen RaceSignals consumed by resolve_termination.
    """

    process_exited: bool = False
    process_returncode: int | None = None
    channel_a_confirmed: bool = False
    channel_b_status: str | None = None

    def to_race_signals(self) -> RaceSignals:
        return RaceSignals(
            process_exited=self.process_exited,
            process_returncode=self.process_returncode,
            channel_a_confirmed=self.channel_a_confirmed,
            channel_b_status=self.channel_b_status,
        )


async def _watch_process(
    proc: anyio.abc.Process,
    acc: RaceAccumulator,
    trigger: anyio.Event,
) -> None:
    """Wait for the subprocess to exit and deposit the process-exit signal."""
    await proc.wait()
    logger.debug("process_exited", pid=proc.pid, returncode=proc.returncode)
    acc.process_exited = True
    acc.process_returncode = proc.returncode
    trigger.set()


async def _watch_heartbeat(
    stdout_path: Path,
    heartbeat_marker: str,
    heartbeat_record_types: frozenset[str],
    acc: RaceAccumulator,
    trigger: anyio.Event,
    _poll_interval: float,
) -> None:
    """Poll stdout NDJSON for a result record and deposit the Channel A signal."""
    await _heartbeat(
        stdout_path, heartbeat_marker, heartbeat_record_types, _poll_interval=_poll_interval
    )
    logger.debug(
        "channel_a_confirmed",
        stdout_path=str(stdout_path),
        record_types=list(heartbeat_record_types),
    )
    acc.channel_a_confirmed = True
    trigger.set()


async def _watch_session_log(
    session_log_dir: Path,
    completion_marker: str,
    stale_threshold: float,
    spawn_time: float,
    session_record_types: frozenset[str],
    pid: int,
    completion_drain_timeout: float,
    acc: RaceAccumulator,
    trigger: anyio.Event,
    channel_b_ready: anyio.Event,
    _phase1_poll: float,
    _phase2_poll: float,
    _phase1_timeout: float,
) -> None:
    """Monitor the session JSONL log and deposit the Channel B signal.

    When the session reports completion (not stale), a drain-wait window
    is opened via anyio.move_on_after so Channel A can fire first if it
    is about to confirm. The trigger is set after the B signal is deposited.
    """
    result = await _session_log_monitor(
        session_log_dir,
        completion_marker,
        stale_threshold,
        spawn_time,
        session_record_types,
        pid=pid,
        _phase1_poll=_phase1_poll,
        _phase2_poll=_phase2_poll,
        _phase1_timeout=_phase1_timeout,
    )
    if result == "completion":
        # Drain-wait: give Channel A a window to confirm before Channel B wins.
        # move_on_after absorbs timeout; trigger may already be set if A fired.
        with anyio.move_on_after(completion_drain_timeout):
            await trigger.wait()
        logger.debug("channel_b_drain_complete", trigger_was_set=trigger.is_set())
    logger.debug("channel_b_result", status=result, drain_window=result == "completion")
    # These writes execute atomically before any cancellation delivery:
    # there is no await between them and the function return.
    acc.channel_b_status = result
    channel_b_ready.set()
    trigger.set()


def resolve_termination(
    signals: RaceSignals,
) -> tuple[TerminationReason, ChannelConfirmation]:
    """Determine termination and channel from accumulated signals.

    Pure function: no side effects. Channel confirmation and termination
    reason are resolved independently so that simultaneous task completion
    never discards a channel signal.

    Priority for termination: process exit > stale > channel win.
    Channel confirmation is independent of termination.
    """
    # Channel confirmation: independent of termination reason
    if signals.channel_a_confirmed:
        channel = ChannelConfirmation.CHANNEL_A
    elif signals.channel_b_status == "completion":
        channel = ChannelConfirmation.CHANNEL_B
    else:
        channel = ChannelConfirmation.UNMONITORED

    # Termination reason: priority order
    if signals.process_exited:
        termination = TerminationReason.NATURAL_EXIT
    elif signals.channel_b_status == "stale":
        termination = TerminationReason.STALE
    elif signals.channel_a_confirmed:
        termination = TerminationReason.COMPLETED
    elif signals.channel_b_status == "completion":
        termination = TerminationReason.COMPLETED
    else:
        termination = TerminationReason.NATURAL_EXIT  # fallback

    logger.debug(
        "resolve_termination",
        process_exited=signals.process_exited,
        process_returncode=signals.process_returncode,
        channel_a_confirmed=signals.channel_a_confirmed,
        channel_b_status=signals.channel_b_status,
        resolved_termination=str(termination),
        resolved_channel=str(channel),
    )
    return termination, channel


async def run_managed_async(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
    input_data: str | None = None,
    env: dict[str, str] | None = None,
    pty_mode: bool = False,
    heartbeat_marker: str | None = None,
    heartbeat_record_types: frozenset[str] = frozenset({"result"}),
    session_log_dir: Path | None = None,
    completion_marker: str = "",
    stale_threshold: float = 1200,
    session_record_types: frozenset[str] = frozenset({"assistant"}),
    completion_drain_timeout: float = 5.0,
    _phase1_poll: float = 1.0,
    _phase2_poll: float = 2.0,
    _heartbeat_poll: float = 0.5,
    _phase1_timeout: float = 30.0,
) -> SubprocessResult:
    """Async subprocess execution with temp file I/O and process tree cleanup.

    Wires all lifecycle utilities together:
    1. create_temp_io for stdout/stderr/stdin
    2. optional PTY wrapping for TTY-dependent CLIs
    3. spawn with start_new_session=True
    4. two-channel race: proc.wait / stdout heartbeat / session log monitor
    5. async_kill_process_tree on failure/timeout/completion-detection
    6. read_temp_output for results
    7. cleanup temp files via context manager

    Args:
        cmd: Command to execute as list of strings.
        cwd: Working directory for the subprocess.
        timeout: Maximum seconds to wait for the process.
        input_data: Optional string to pass as stdin.
        env: Optional environment variables (defaults to os.environ).
        pty_mode: Wrap command with ``script`` to provide a PTY.
        heartbeat_marker: Substring to watch for in stdout NDJSON output.
        heartbeat_record_types: JSONL record types to scan for heartbeat marker.
        session_log_dir: Directory to watch for Claude session JSONL files.
        completion_marker: Marker to watch for in session log content.
        stale_threshold: Seconds of session log inactivity before "stale".
        session_record_types: JSONL record types to scan for completion marker.
        completion_drain_timeout: Seconds to wait for Channel A (heartbeat) to
            confirm stdout data after Channel B (session monitor) signals
            completion. Prevents false-negative failures from the Channel B /
            Channel A race where the session monitor fires before the CLI
            flushes its result record.
        _phase1_poll: Interval in seconds for session monitor Phase 1 (JSONL file
            discovery) polling.
        _phase2_poll: Interval in seconds for session monitor Phase 2 (completion
            marker scan) polling.
        _heartbeat_poll: Interval in seconds for heartbeat (stdout record type) polling.
    """
    if pty_mode:
        cmd = pty_wrap_command(cmd)

    with create_temp_io(input_data) as (stdout_file, stderr_file, stdin_path):
        stdout_path = Path(stdout_file.name)
        stderr_path = Path(stderr_file.name)

        # Build stdin handle — use DEVNULL when no input to prevent
        # inheriting parent's stdin (e.g. MCP server socket that never closes)
        stdin_handle = None
        if stdin_path is not None:
            stdin_handle = open(stdin_path)  # noqa: SIM115

        try:
            proc = await anyio.open_process(
                cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )

            termination = TerminationReason.NATURAL_EXIT
            _channel_confirmation = ChannelConfirmation.UNMONITORED

            proc_log = logger.bind(pid=proc.pid)
            proc_log.debug(
                "run_managed_async_entry",
                cmd_summary=cmd[0] if cmd else "<empty>",
                cwd=str(cwd),
                timeout=timeout,
                stale_threshold=stale_threshold,
                session_log_dir=str(session_log_dir) if session_log_dir else None,
                heartbeat_enabled=bool(heartbeat_marker),
                session_monitor_enabled=session_log_dir is not None,
            )

            acc = RaceAccumulator()
            trigger = anyio.Event()
            channel_b_ready = anyio.Event()
            timeout_scope = None  # bound inside task group body; initialized for safety

            async with anyio.create_task_group() as tg:
                tg.start_soon(_watch_process, proc, acc, trigger)
                if heartbeat_marker:
                    tg.start_soon(
                        _watch_heartbeat,
                        stdout_path,
                        heartbeat_marker,
                        heartbeat_record_types,
                        acc,
                        trigger,
                        _heartbeat_poll,
                    )
                if session_log_dir is not None:
                    tg.start_soon(
                        _watch_session_log,
                        session_log_dir,
                        completion_marker,
                        stale_threshold,
                        time.time(),
                        session_record_types,
                        proc.pid,
                        completion_drain_timeout,
                        acc,
                        trigger,
                        channel_b_ready,
                        _phase1_poll,
                        _phase2_poll,
                        _phase1_timeout,
                    )
                with anyio.move_on_after(timeout) as timeout_scope:
                    await trigger.wait()
                # Symmetric drain: if the process exited before Channel B deposited,
                # give the session monitor a bounded window to complete its current
                # poll cycle and deposit its signal.
                if (
                    acc.process_exited
                    and acc.channel_b_status is None
                    and session_log_dir is not None
                ):
                    proc_log.debug(
                        "symmetric_drain_started",
                        reason="process_exited_before_channel_b",
                        drain_timeout=completion_drain_timeout,
                    )
                    with anyio.move_on_after(completion_drain_timeout):
                        await channel_b_ready.wait()
                    proc_log.debug(
                        "symmetric_drain_complete",
                        channel_b_status=acc.channel_b_status,
                        channel_b_deposited=acc.channel_b_status is not None,
                    )
                tg.cancel_scope.cancel()

            signals = acc.to_race_signals()
            termination, _channel_confirmation = resolve_termination(signals)

            if timeout_scope.cancelled_caught:
                termination = TerminationReason.TIMED_OUT
                proc_log.debug("kill_decision", reason="timeout", timeout=timeout)
                logger.warning("Process %d timed out after %ss, killing tree", proc.pid, timeout)
                await async_kill_process_tree(proc.pid)
            elif signals.process_exited:
                proc_log.debug(
                    "kill_decision",
                    reason="natural_exit",
                    returncode=signals.process_returncode,
                )
            elif termination == TerminationReason.STALE:
                proc_log.debug("kill_decision", reason="stale", stale_threshold=stale_threshold)
                logger.warning("Session stale for %ss, killing tree", stale_threshold)
                await async_kill_process_tree(proc.pid)
            else:
                proc_log.debug(
                    "kill_decision",
                    reason="channel_won",
                    channel_a=signals.channel_a_confirmed,
                    channel_b=signals.channel_b_status,
                )
                # Channel A or B won; process still alive — kill immediately.
                await async_kill_process_tree(proc.pid)

            # Flush and close before reading
            stdout_file.close()
            stderr_file.close()

            stdout, stderr = read_temp_output(stdout_path, stderr_path)

            sub_result = SubprocessResult(
                returncode=proc.returncode if proc.returncode is not None else -1,
                stdout=stdout,
                stderr=stderr,
                termination=termination,
                pid=proc.pid,
                channel_confirmation=_channel_confirmation,
            )
            proc_log.debug(
                "run_managed_async_result",
                returncode=sub_result.returncode,
                termination=str(sub_result.termination),
                channel=str(sub_result.channel_confirmation),
                stdout_len=len(sub_result.stdout),
                stderr_len=len(sub_result.stderr),
            )
            return sub_result
        except BaseException:
            # Ensure cleanup on unexpected errors (including CancelledError)
            if "proc" in locals() and proc.returncode is None:
                kill_process_tree(proc.pid)
            raise
        finally:
            if stdin_handle is not None:
                try:
                    stdin_handle.close()
                except OSError:
                    pass


def run_managed_sync(
    cmd: list[str],
    *,
    cwd: Path | None,
    timeout: float,
    input_data: str | None = None,
    env: dict[str, str] | None = None,
) -> SubprocessResult:
    """Sync subprocess execution with temp file I/O and process tree cleanup.

    Same composition pattern as run_managed_async but uses subprocess.Popen
    with start_new_session=True.

    Args:
        cmd: Command to execute as list of strings.
        cwd: Working directory for the subprocess (None for current dir).
        timeout: Maximum seconds to wait for the process.
        input_data: Optional string to pass as stdin.
        env: Optional environment variables (defaults to os.environ).
    """
    with create_temp_io(input_data) as (stdout_file, stderr_file, stdin_path):
        stdout_path = Path(stdout_file.name)
        stderr_path = Path(stderr_file.name)

        # Build stdin handle — use DEVNULL when no input to prevent
        # inheriting parent's stdin (e.g. MCP server socket that never closes)
        stdin_handle = None
        if stdin_path is not None:
            stdin_handle = open(stdin_path)  # noqa: SIM115

        process = None
        try:
            process = subprocess.Popen(
                cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )

            termination = TerminationReason.NATURAL_EXIT
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                termination = TerminationReason.TIMED_OUT
                logger.warning(
                    "Process %d timed out after %ss, killing tree",
                    process.pid,
                    timeout,
                )
                kill_process_tree(process.pid)

            # Flush and close before reading
            stdout_file.close()
            stderr_file.close()

            stdout, stderr = read_temp_output(stdout_path, stderr_path)

            return SubprocessResult(
                returncode=process.returncode if process.returncode is not None else -1,
                stdout=stdout,
                stderr=stderr,
                termination=termination,
                pid=process.pid,
                channel_confirmation=ChannelConfirmation.UNMONITORED,
            )
        except Exception:
            if process is not None and process.returncode is None:
                kill_process_tree(process.pid)
            raise
        finally:
            if stdin_handle is not None:
                try:
                    stdin_handle.close()
                except OSError:
                    pass


class DefaultSubprocessRunner:
    """Implements SubprocessRunner protocol by delegating to run_managed_async."""

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        heartbeat_marker: str = "",
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = False,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
    ) -> SubprocessResult:
        return await run_managed_async(
            cmd,
            cwd=cwd,
            timeout=timeout,
            heartbeat_marker=heartbeat_marker if heartbeat_marker else None,
            stale_threshold=stale_threshold,
            completion_marker=completion_marker,
            session_log_dir=session_log_dir,
            pty_mode=pty_mode,
            input_data=input_data,
            completion_drain_timeout=completion_drain_timeout,
        )
