"""Owned POSIX process groups for the isolated shell runner.

The helper is intentionally stdlib-only.  It creates the child group
atomically, forwards terminal signals, restores inherited terminal state, and
does not return until the leader is reaped and the owned group is absent.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import IO, Any

_TERM_TIMEOUT_SECONDS = 2.0
_KILL_TIMEOUT_SECONDS = 2.0
_GROUP_POLL_SECONDS = 0.02
_FORWARDED_SIGNALS = (
    signal.SIGINT,
    signal.SIGTERM,
    signal.SIGHUP,
    signal.SIGQUIT,
)
logger = logging.getLogger(__name__)  # noqa: TID251 - isolated stdlib runner
logger.addHandler(logging.NullHandler())
logger.propagate = False


class OwnedProcessError(RuntimeError):
    """The runner could not prove complete process-group settlement."""


@dataclass(slots=True)
class OwnedProcessGroup:
    """One child leader and every descendant that remains in its process group."""

    process: subprocess.Popen[bytes]
    pgid: int
    _previous_handlers: dict[signal.Signals, Any] = field(default_factory=dict)
    _terminal_fd: int | None = None
    _previous_foreground_pgid: int | None = None
    _restored: bool = False

    @property
    def stdout(self) -> IO[bytes] | None:
        return self.process.stdout

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    def poll(self) -> int | None:
        return self.process.poll()

    def terminate(self) -> None:
        self.signal_group(signal.SIGTERM)

    def kill(self) -> None:
        self.signal_group(signal.SIGKILL)

    def signal_group(self, signum: signal.Signals) -> None:
        _signal_process_group(self.pgid, signum)

    def wait(self) -> int:
        """Wait for the leader, settle remaining group members, and restore state."""

        failure: BaseException | None = None
        returncode: int | None = None
        try:
            returncode = self.process.wait()
            self._settle_remaining_group()
        except BaseException as exc:
            logger.error("owned_process_wait_failed", exc_info=True)
            failure = exc
            try:
                self.settle()
            except BaseException as cleanup_exc:
                logger.error("owned_process_wait_cleanup_failed", exc_info=True)
                raise BaseExceptionGroup(
                    "owned process wait and cleanup failed",
                    [exc, cleanup_exc],
                ) from None
        finally:
            self._restore_parent_state()
        if failure is not None:
            raise failure
        if returncode is None:
            raise OwnedProcessError("owned process leader has no return code")
        return returncode

    def settle(self) -> int:
        """Terminate the group when necessary, reap the leader, and prove absence."""

        failures: list[BaseException] = []
        try:
            if self.process.poll() is None:
                self.signal_group(signal.SIGTERM)
                try:
                    self.process.wait(timeout=_TERM_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    self.signal_group(signal.SIGKILL)
                    try:
                        self.process.wait(timeout=_KILL_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired as exc:
                        failures.append(
                            OwnedProcessError(
                                f"owned process leader {self.process.pid} was not reaped"
                            )
                        )
                        failures.append(exc)
            elif self.process.returncode is None:
                self.process.wait(timeout=_KILL_TIMEOUT_SECONDS)

            try:
                self._settle_remaining_group()
            except BaseException as exc:
                logger.error("owned_process_group_cleanup_failed", exc_info=True)
                failures.append(exc)
        finally:
            try:
                self._restore_parent_state()
            except BaseException as exc:
                logger.error("owned_process_parent_restore_failed", exc_info=True)
                failures.append(exc)

        if failures:
            if len(failures) == 1:
                raise failures[0]
            raise BaseExceptionGroup("owned process cleanup failed", failures)
        returncode = self.process.returncode
        if returncode is None:
            raise OwnedProcessError("owned process leader has no return code")
        return returncode

    def _settle_remaining_group(self) -> None:
        if not _process_group_exists(self.pgid):
            return
        self.signal_group(signal.SIGTERM)
        if not _wait_for_group_exit(self.pgid, _TERM_TIMEOUT_SECONDS):
            self.signal_group(signal.SIGKILL)
            if not _wait_for_group_exit(self.pgid, _KILL_TIMEOUT_SECONDS):
                raise OwnedProcessError(f"owned process group {self.pgid} survived SIGKILL")

    def _restore_parent_state(self) -> None:
        if self._restored:
            return
        self._restored = True
        failures: list[BaseException] = []
        if self._terminal_fd is not None and self._previous_foreground_pgid is not None:
            try:
                _safe_tcsetpgrp(
                    self._terminal_fd,
                    self._previous_foreground_pgid,
                )
            except BaseException as exc:
                logger.error("owned_process_foreground_restore_failed", exc_info=True)
                failures.append(exc)
        for signum, previous in self._previous_handlers.items():
            try:
                signal.signal(signum, previous)  # noqa: TID251
            except BaseException as exc:
                logger.error("owned_process_signal_restore_failed", exc_info=True)
                failures.append(exc)
        if failures:
            if len(failures) == 1:
                raise failures[0]
            raise BaseExceptionGroup("parent process state restoration failed", failures)


def spawn_owned_process(
    argv: Sequence[str],
    *,
    cwd_fd: int,
    env: Mapping[str, str],
    capture_output: bool,
) -> OwnedProcessGroup:
    """Spawn one child in a fresh process group beneath a descriptor cwd."""

    _require_posix_process_ownership()
    original_cwd_fd = os.open(".", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    process: subprocess.Popen[bytes] | None = None
    restore_error: OSError | None = None
    try:
        os.fchdir(cwd_fd)
        process = subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.STDOUT if capture_output else None,
            close_fds=True,
            env=dict(env),
            process_group=0,
            start_new_session=False,
        )
    finally:
        try:
            os.fchdir(original_cwd_fd)
        except OSError as exc:
            restore_error = exc
        finally:
            os.close(original_cwd_fd)

    if process is None:
        raise OwnedProcessError("owned process did not start")
    if restore_error is not None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=_KILL_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError):
            pass
        raise OwnedProcessError("cannot restore runner cwd") from restore_error
    return adopt_owned_process(process, inherit_terminal=not capture_output)


def adopt_owned_process(
    process: subprocess.Popen[bytes],
    *,
    inherit_terminal: bool,
) -> OwnedProcessGroup:
    """Attach signal, terminal, and settlement ownership to a fresh child group."""

    pgid = process.pid
    if pgid <= 1:
        try:
            process.kill()
            process.wait(timeout=_KILL_TIMEOUT_SECONDS)
        finally:
            raise OwnedProcessError("unsafe owned process group identity")

    owner = OwnedProcessGroup(process=process, pgid=pgid)
    try:
        owner._previous_handlers = _install_signal_forwarding(owner)
        if inherit_terminal:
            terminal = _take_foreground_process_group(pgid)
            if terminal is not None:
                owner._terminal_fd, owner._previous_foreground_pgid = terminal
    except BaseException:
        logger.error("owned_process_adoption_failed", exc_info=True)
        owner.settle()
        raise
    return owner


def _install_signal_forwarding(
    owner: OwnedProcessGroup,
) -> dict[signal.Signals, Any]:
    previous: dict[signal.Signals, Any] = {}

    def forward(signum: int, _frame: object) -> None:
        try:
            owner.signal_group(signal.Signals(signum))
        except (OSError, ValueError):
            return

    try:
        for signum in _FORWARDED_SIGNALS:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, forward)  # noqa: TID251
    except BaseException:
        logger.error("owned_process_signal_install_failed", exc_info=True)
        for signum, handler in previous.items():
            signal.signal(signum, handler)  # noqa: TID251
        raise
    return previous


def _take_foreground_process_group(pgid: int) -> tuple[int, int] | None:
    if not hasattr(os, "tcgetpgrp") or not hasattr(os, "tcsetpgrp"):
        return None
    try:
        terminal_fd = sys.stdin.fileno()
    except (OSError, TypeError, ValueError):
        return None
    if not os.isatty(terminal_fd):
        return None
    previous_pgid = os.tcgetpgrp(terminal_fd)
    _safe_tcsetpgrp(terminal_fd, pgid)
    return terminal_fd, previous_pgid


def _safe_tcsetpgrp(terminal_fd: int, pgid: int) -> None:
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
    try:
        os.tcsetpgrp(terminal_fd, pgid)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _require_posix_process_ownership() -> None:
    if os.name != "posix" or not hasattr(os, "killpg"):
        raise OwnedProcessError("shell runner requires POSIX process-group ownership")


def _wait_for_group_exit(pgid: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if not _process_group_exists(pgid):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_GROUP_POLL_SECONDS, remaining))


def _process_group_exists(pgid: int) -> bool:
    if pgid <= 1:
        raise OwnedProcessError("unsafe owned process group identity")
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_process_group(pgid: int, signum: signal.Signals) -> None:
    if pgid <= 1:
        raise OwnedProcessError("unsafe owned process group identity")
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return


__all__ = [
    "adopt_owned_process",
    "OwnedProcessError",
    "OwnedProcessGroup",
    "spawn_owned_process",
]
