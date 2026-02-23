"""Subprocess lifecycle utilities providing pipe-blocking immunity.

Shared building blocks for all subprocess-spawning code in the project.
Uses temp file I/O (not pipes) to eliminate FD-inheritance blocking, and
psutil-based process tree cleanup with SIGTERM→SIGKILL escalation.

Two composed functions wire the utilities together correctly:
- ``run_managed_async`` for async callers
- ``run_managed_sync`` for sync callers
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import psutil

logger = logging.getLogger(__name__)


@dataclass
class SubprocessResult:
    """Result from a managed subprocess execution."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    pid: int
    stale: bool = False


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
    """Non-blocking wrapper around kill_process_tree for asyncio callers."""
    await asyncio.to_thread(kill_process_tree, pid, timeout)


def pty_wrap_command(cmd: list[str]) -> list[str]:
    """Wrap a command with ``script`` to provide a PTY.

    Returns the original command unchanged if ``script`` is not available.
    """
    script_path = shutil.which("script")
    if script_path is None:
        return cmd
    escaped = " ".join(shlex.quote(c) for c in cmd)
    return [script_path, "-qefc", escaped, "/dev/null"]


async def _heartbeat(stdout_path: Path, marker: str) -> str:
    """Poll session NDJSON output for a completion event."""
    scan_pos = 0
    os_error_count = 0
    while True:
        await asyncio.sleep(0.5)
        try:
            content = stdout_path.read_text(errors="replace")
            os_error_count = 0
        except OSError:
            os_error_count += 1
            if os_error_count == 10:
                logger.warning("Heartbeat: 10 consecutive read failures on %s", stdout_path)
            continue
        new_content = content[scan_pos:]
        scan_pos = len(content)
        if marker in new_content:
            return "completion"


async def _session_log_monitor(
    session_log_dir: Path,
    completion_marker: str,
    stale_threshold: float,
    spawn_time: float,
) -> str:
    """Watch Claude Code session log for completion or staleness.

    Finds the session JSONL file (newest in session_log_dir created after
    spawn_time), then monitors it for:
    - completion_marker in new content -> return "completion"
    - No mtime change for stale_threshold seconds -> return "stale"
    """
    # Phase 1: Find the session log file
    session_file = None
    os_error_count = 0
    while session_file is None:
        await asyncio.sleep(1.0)
        try:
            candidates = [
                f
                for f in session_log_dir.iterdir()
                if f.suffix == ".jsonl" and f.stat().st_ctime > spawn_time
            ]
            if candidates:
                session_file = max(candidates, key=lambda f: f.stat().st_ctime)
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
    last_change = asyncio.get_event_loop().time()
    scan_pos = 0
    os_error_count = 0

    while True:
        await asyncio.sleep(2.0)
        try:
            current_size = session_file.stat().st_size
            os_error_count = 0
        except OSError:
            os_error_count += 1
            if os_error_count == 10:
                logger.warning(
                    "Session monitor: 10 consecutive stat failures on %s", session_file
                )
            continue

        if current_size > last_size:
            last_size = current_size
            last_change = asyncio.get_event_loop().time()

            # Check new content for completion marker
            try:
                content = session_file.read_text(errors="replace")
                new_content = content[scan_pos:]
                scan_pos = len(content)
                if completion_marker in new_content:
                    return "completion"
            except OSError:
                pass
        else:
            # Check staleness
            elapsed = asyncio.get_event_loop().time() - last_change
            if elapsed >= stale_threshold:
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


async def run_managed_async(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
    input_data: str | None = None,
    env: dict[str, str] | None = None,
    pty_mode: bool = False,
    heartbeat_marker: str | None = None,
    session_log_dir: Path | None = None,
    completion_marker: str = "",
    stale_threshold: float = 1200,
) -> SubprocessResult:
    """Async subprocess execution with temp file I/O and process tree cleanup.

    Wires all lifecycle utilities together:
    1. create_temp_io for stdout/stderr/stdin
    2. optional PTY wrapping for TTY-dependent CLIs
    3. spawn with process_group=0
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
        session_log_dir: Directory to watch for Claude session JSONL files.
        completion_marker: Marker to watch for in session log content.
        stale_threshold: Seconds of session log inactivity before "stale".
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
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                cwd=cwd,
                env=env,
                process_group=0,
            )

            timed_out = False
            stale = False

            # Build the race participants
            wait_task = asyncio.create_task(proc.wait())
            tasks: set[asyncio.Task] = {wait_task}

            heartbeat_task = None
            if heartbeat_marker:
                heartbeat_task = asyncio.create_task(_heartbeat(stdout_path, heartbeat_marker))
                tasks.add(heartbeat_task)

            session_monitor_task = None
            if session_log_dir is not None:
                import time

                session_monitor_task = asyncio.create_task(
                    _session_log_monitor(
                        session_log_dir,
                        completion_marker,
                        stale_threshold,
                        time.time(),
                    )
                )
                tasks.add(session_monitor_task)

            try:
                done, pending = await asyncio.wait_for(
                    asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED),
                    timeout=timeout,
                )

                # Priority: wait_task > heartbeat/monitor
                # If the process exited on its own, its result is authoritative
                # regardless of what the monitor detected.
                if wait_task in done:
                    pass
                elif session_monitor_task in done and session_monitor_task.result() == "stale":
                    stale = True
                    logger.warning("Session stale for %ss, killing tree", stale_threshold)
                    await async_kill_process_tree(proc.pid)
                else:
                    await async_kill_process_tree(proc.pid)
            except TimeoutError:
                timed_out = True
                logger.warning("Process %d timed out after %ss, killing tree", proc.pid, timeout)
                await async_kill_process_tree(proc.pid)
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()

            # Flush and close before reading
            stdout_file.close()
            stderr_file.close()

            stdout, stderr = read_temp_output(stdout_path, stderr_path)

            return SubprocessResult(
                returncode=proc.returncode if proc.returncode is not None else -1,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                pid=proc.pid,
                stale=stale,
            )
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

            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
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
                timed_out=timed_out,
                pid=process.pid,
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
