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
        pass
    try:
        stderr = stderr_path.read_text(errors="replace")
    except OSError:
        pass
    return stdout, stderr


async def run_managed_async(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float,
    input_data: str | None = None,
    env: dict[str, str] | None = None,
) -> SubprocessResult:
    """Async subprocess execution with temp file I/O and process tree cleanup.

    Wires all lifecycle utilities together:
    1. create_temp_io for stdout/stderr/stdin
    2. spawn with process_group=0
    3. wait with independent timeout
    4. kill_process_tree on failure/timeout
    5. read_temp_output for results
    6. cleanup temp files via context manager

    Args:
        cmd: Command to execute as list of strings.
        cwd: Working directory for the subprocess.
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
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except TimeoutError:
                timed_out = True
                logger.warning("Process %d timed out after %ss, killing tree", proc.pid, timeout)
                kill_process_tree(proc.pid)

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
            )
        except Exception:
            # Ensure cleanup on unexpected errors
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
