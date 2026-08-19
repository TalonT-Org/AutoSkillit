"""POSIX process ownership for one interactive cook attempt."""

from __future__ import annotations

import contextlib
import os
import signal
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
from autoskillit.execution import (
    INTERACTIVE_TETHER_CEILING_SECONDS,
    OwnedProcessGroup,
    TetherSpec,
    spawn_owned_process,
    wrap_systemd_scope,
)

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
    not_after: float,
    systemd_scope_enabled: bool = False,
) -> CookAttemptResult:
    """Run one finalized cook command and prove complete child cleanup.

    ``not_after`` (epoch seconds) is the live spawner's own ceiling: both wait
    branches terminate the child once it passes, independent of the tether
    sweep, which only ever matters once this process itself has died.
    """
    _require_posix_process_ownership()
    cwd = _canonical_cwd(spec.cwd)
    inherited_fds = _normalize_pass_fds(pass_fds)

    owner: OwnedProcessGroup | None = None
    pid: int | None = None
    pgid: int | None = None
    returncode: int | None = None
    master_fd: int | None = None
    slave_fd: int | None = None
    failures: list[BaseException] = []

    with terminal_guard():
        try:
            if observer is None:
                owner = spawn_owned_process(
                    wrap_systemd_scope(
                        list(spec.cmd),
                        enabled=systemd_scope_enabled,
                        ceiling_seconds=INTERACTIVE_TETHER_CEILING_SECONDS,
                    ),
                    cwd=cwd,
                    env=dict(spec.env),
                    pass_fds=inherited_fds,
                    process_group=0,
                    start_new_session=False,
                    tether=TetherSpec(
                        origin="cook", ceiling_seconds=INTERACTIVE_TETHER_CEILING_SECONDS
                    ),
                )
            else:
                master_fd, slave_fd = os.openpty()
                launcher_fds = _merge_launcher_fds(inherited_fds, slave_fd)
                # systemd-run wraps the PTY launcher (script(1)) itself here, not
                # the workload it execvpe's into — same leader-wrapping shape as
                # the non-PTY branch above, one process earlier in the chain.
                owner = spawn_owned_process(
                    wrap_systemd_scope(
                        launcher_argv(
                            slave_fd,
                            spec.cmd,
                            lease_fds=inherited_fds,
                        ),
                        enabled=systemd_scope_enabled,
                        ceiling_seconds=INTERACTIVE_TETHER_CEILING_SECONDS,
                    ),
                    cwd=cwd,
                    env=dict(spec.env),
                    pass_fds=launcher_fds,
                    start_new_session=True,
                    tether=TetherSpec(
                        origin="cook", ceiling_seconds=INTERACTIVE_TETHER_CEILING_SECONDS
                    ),
                )

            pid = owner.pid
            pgid = owner.pgid
            on_spawn(pid, pgid)
            trace.record_spawn()

            if observer is None:
                with _foreground_process_group(pgid):
                    _wait_for_owned_exit(owner, not_after)
            else:
                assert master_fd is not None
                assert slave_fd is not None
                os.close(slave_fd)
                slave_fd = None
                observer.relay(
                    master_fd,
                    cancelled=lambda: owner.observe_exit() is not None or time.time() >= not_after,
                )
                master_fd = None
        except BaseException as exc:
            logger.error("cook_attempt_failed", error_type=type(exc).__name__)
            failures.append(exc)
        finally:
            if slave_fd is not None:
                try:
                    os.close(slave_fd)
                except BaseException as exc:
                    logger.error(
                        "cook_slave_fd_close_failed",
                        error_type=type(exc).__name__,
                    )
                    failures.append(exc)
                slave_fd = None
            if master_fd is not None:
                try:
                    if observer is None:
                        os.close(master_fd)
                    else:
                        observer.close_master(master_fd)
                except BaseException as exc:
                    logger.error(
                        "cook_master_fd_close_failed",
                        error_type=type(exc).__name__,
                    )
                    failures.append(exc)
                master_fd = None

            cleanup_proved = False
            if owner is not None and pid is not None and pgid is not None:
                try:
                    if failures:
                        cleanup_result = owner.settle_preserving(failures[0])
                        returncode = owner.process.returncode
                        cleanup_proved = returncode is not None and cleanup_result.complete
                    else:
                        returncode, cleanup_result = owner.settle()
                        cleanup_proved = cleanup_result.complete
                except BaseException as exc:
                    logger.error(
                        "cook_process_cleanup_failed",
                        error_type=type(exc).__name__,
                        pid=pid,
                        pgid=pgid,
                    )
                    failures.append(exc)
                if cleanup_proved:
                    try:
                        on_reaped(pid, pgid)
                    except BaseException as exc:
                        logger.error(
                            "cook_reap_callback_failed",
                            error_type=type(exc).__name__,
                            pid=pid,
                            pgid=pgid,
                        )
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


def _wait_for_owned_exit(owner: OwnedProcessGroup, not_after: float) -> None:
    """Cooperatively observe an owned child without releasing its reap fence.

    Returns once the child has exited OR its ceiling has passed — in the
    ceiling case the child is very likely still alive, and the caller's
    unconditional settle()/settle_preserving() cleanup terminates it.
    """
    while owner.observe_exit() is None and time.time() < not_after:
        time.sleep(_GROUP_POLL_SECONDS)


__all__ = ["run_cook_attempt"]
