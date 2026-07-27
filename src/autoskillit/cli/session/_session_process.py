"""POSIX process ownership for one interactive cook attempt."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from autoskillit.cli.session._session_startup_trace import StartupTrace
from autoskillit.cli.session.pty._exec import launcher_argv
from autoskillit.cli.session.pty._observer import PtyObserver
from autoskillit.cli.ui._terminal import terminal_guard
from autoskillit.core import CmdSpec, get_logger

_TERM_TIMEOUT_SECONDS = 2.0
_KILL_TIMEOUT_SECONDS = 2.0
_GROUP_POLL_SECONDS = 0.02
logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CookAttemptResult:
    """Proof that a spawned attempt was fully terminated and reaped."""

    pid: int
    pgid: int
    returncode: int


def run_cook_attempt(
    spec: CmdSpec,
    *,
    pass_fds: tuple[int, ...],
    on_spawn: Callable[[int, int], None],
    on_reaped: Callable[[int, int], None],
    trace: StartupTrace,
    observer: PtyObserver | None,
) -> CookAttemptResult:
    """Run one finalized cook command and prove complete child cleanup."""
    _require_posix_process_ownership()
    cwd = _canonical_cwd(spec.cwd)
    inherited_fds = _normalize_pass_fds(pass_fds)

    process: subprocess.Popen[bytes] | None = None
    pid: int | None = None
    pgid: int | None = None
    returncode: int | None = None
    master_fd: int | None = None
    slave_fd: int | None = None
    failures: list[BaseException] = []

    with terminal_guard():
        try:
            if observer is None:
                process = subprocess.Popen(
                    spec.cmd,
                    cwd=cwd,
                    env=dict(spec.env),
                    pass_fds=inherited_fds,
                    process_group=0,
                    start_new_session=False,
                )
            else:
                master_fd, slave_fd = os.openpty()
                launcher_fds = _merge_launcher_fds(inherited_fds, slave_fd)
                process = subprocess.Popen(
                    launcher_argv(
                        slave_fd,
                        spec.cmd,
                        lease_fds=inherited_fds,
                    ),
                    cwd=cwd,
                    env=dict(spec.env),
                    pass_fds=launcher_fds,
                    start_new_session=True,
                )

            pid = process.pid
            pgid = pid
            on_spawn(pid, pgid)
            trace.record_spawn()

            if observer is None:
                with _foreground_process_group(pgid):
                    returncode = process.wait()
            else:
                assert master_fd is not None
                assert slave_fd is not None
                os.close(slave_fd)
                slave_fd = None
                observer.relay(master_fd)
                master_fd = None
                returncode = process.wait()
        except BaseException as exc:
            logger.error("cook_attempt_failed", exc_info=True)
            failures.append(exc)
        finally:
            if slave_fd is not None:
                try:
                    os.close(slave_fd)
                except BaseException as exc:
                    logger.error("cook_slave_fd_close_failed", exc_info=True)
                    failures.append(exc)
                slave_fd = None
            if master_fd is not None:
                try:
                    if observer is None:
                        os.close(master_fd)
                    else:
                        observer.close_master(master_fd)
                except BaseException as exc:
                    logger.error("cook_master_fd_close_failed", exc_info=True)
                    failures.append(exc)
                master_fd = None

            cleanup_proved = False
            if process is not None and pid is not None and pgid is not None:
                try:
                    returncode = _terminate_reap_and_verify(process, pgid)
                    cleanup_proved = True
                except BaseException as exc:
                    logger.error("cook_process_cleanup_failed", exc_info=True)
                    failures.append(exc)
                if cleanup_proved:
                    try:
                        on_reaped(pid, pgid)
                    except BaseException as exc:
                        logger.error("cook_reap_callback_failed", exc_info=True)
                        failures.append(exc)

    if failures:
        if len(failures) == 1:
            raise failures[0]
        raise BaseExceptionGroup("cook attempt and cleanup failed", failures)
    if pid is None or pgid is None or returncode is None:
        raise RuntimeError("cook attempt completed without process ownership proof")
    return CookAttemptResult(pid=pid, pgid=pgid, returncode=returncode)


def _require_posix_process_ownership() -> None:
    if os.name != "posix" or not hasattr(os, "killpg") or not hasattr(os, "tcsetpgrp"):
        raise RuntimeError("interactive cook requires POSIX process-group ownership")


def _canonical_cwd(value: str) -> str:
    if not value:
        raise ValueError("cook command must contain a canonical project cwd")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("cook command cwd must be absolute")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("cook command cwd must be an existing directory")
    if str(resolved) != value:
        raise ValueError("cook command cwd must already be canonical")
    return value


def _normalize_pass_fds(pass_fds: tuple[int, ...]) -> tuple[int, ...]:
    normalized: dict[int, None] = {}
    for descriptor in pass_fds:
        if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 0:
            raise ValueError("pass_fds must contain non-negative integer descriptors")
        os.fstat(descriptor)
        normalized.setdefault(descriptor, None)
    return tuple(normalized)


def _merge_launcher_fds(
    inherited_fds: tuple[int, ...],
    slave_fd: int,
) -> tuple[int, ...]:
    """Preserve lease priority while including the PTY slave exactly once."""
    return tuple(dict.fromkeys((*inherited_fds, slave_fd)))


@contextlib.contextmanager
def _foreground_process_group(pgid: int) -> Iterator[None]:
    """Temporarily transfer an inherited controlling terminal to the child."""
    try:
        terminal_fd = sys.stdin.fileno()
    except (OSError, TypeError, ValueError):
        terminal_fd = None
    if terminal_fd is None or not os.isatty(terminal_fd):
        yield
        return

    previous_pgid = os.tcgetpgrp(terminal_fd)
    _safe_tcsetpgrp(terminal_fd, pgid)

    try:
        yield
    finally:
        _safe_tcsetpgrp(terminal_fd, previous_pgid)


def _safe_tcsetpgrp(terminal_fd: int, pgid: int) -> None:
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
    try:
        os.tcsetpgrp(terminal_fd, pgid)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _terminate_reap_and_verify(
    process: subprocess.Popen[bytes],
    pgid: int,
) -> int:
    """Terminate the entire group, reap its leader, and prove group absence."""
    if _process_group_exists(pgid):
        _signal_process_group(pgid, signal.SIGTERM)
        if not _wait_for_group_exit(process, pgid, _TERM_TIMEOUT_SECONDS):
            _signal_process_group(pgid, signal.SIGKILL)
            if not _wait_for_group_exit(process, pgid, _KILL_TIMEOUT_SECONDS):
                raise RuntimeError(f"cook process group {pgid} survived SIGKILL")

    if process.returncode is None:
        try:
            process.wait(timeout=_KILL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_process_group(pgid, signal.SIGKILL)
            try:
                process.wait(timeout=_KILL_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"cook direct child {process.pid} was not reaped") from exc

    if _process_group_exists(pgid):
        _signal_process_group(pgid, signal.SIGKILL)
        if not _wait_for_group_exit(process, pgid, _KILL_TIMEOUT_SECONDS):
            raise RuntimeError(f"cook process group {pgid} is not empty after reap")

    returncode = process.returncode
    if returncode is None:  # pragma: no cover - guarded by wait above
        raise RuntimeError(f"cook direct child {process.pid} has no return code")
    return returncode


def _wait_for_group_exit(
    process: subprocess.Popen[bytes],
    pgid: int,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        process.poll()
        if not _process_group_exists(pgid):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_GROUP_POLL_SECONDS, remaining))


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _signal_process_group(pgid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return


__all__ = ["run_cook_attempt"]
