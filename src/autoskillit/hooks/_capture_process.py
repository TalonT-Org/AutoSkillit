"""Owned POSIX process groups for the isolated shell runner.

The helper is intentionally stdlib-only.  It creates the child group
atomically, forwards terminal signals, restores inherited terminal state, and
does not return until the leader is reaped and the owned group is absent.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _replay as _capture_replay
    from autoskillit.hooks._capture._authority import (
        _DIRECTORY_FLAGS,
        _READ_FLAGS,
        _UNTRUSTED_WRITE_BITS,
        CaptureSetupError,
    )
    from autoskillit.hooks._capture._snapshot import CaptureMeasurement
    from autoskillit.hooks._capture_contract import PROTECTED_CAPTURE_ENV_VARS
elif __package__:
    from ._capture import _replay as _capture_replay
    from ._capture._authority import (
        _DIRECTORY_FLAGS,
        _READ_FLAGS,
        _UNTRUSTED_WRITE_BITS,
        CaptureSetupError,
    )
    from ._capture._snapshot import CaptureMeasurement
    from ._capture_contract import PROTECTED_CAPTURE_ENV_VARS
else:
    from _capture import _replay as _capture_replay
    from _capture._authority import (
        _DIRECTORY_FLAGS,
        _READ_FLAGS,
        _UNTRUSTED_WRITE_BITS,
        CaptureSetupError,
    )
    from _capture._snapshot import CaptureMeasurement
    from _capture_contract import PROTECTED_CAPTURE_ENV_VARS

_TERM_TIMEOUT_SECONDS = 2.0
_KILL_TIMEOUT_SECONDS = 2.0
_GROUP_POLL_SECONDS = 0.02
_DRAIN_CHUNK_BYTES = 64 * 1024
_POST_EXIT_TERM_SECONDS = 0.25
_POST_EXIT_KILL_SECONDS = 0.5
_DRAIN_POLL_SECONDS = 0.05
_TRUSTED_BASH_CANDIDATES = ("/bin/bash", "/usr/bin/bash")
_PROC_ROOT = "/proc"
_PROC_STAT_READ_BYTES = 4096
_PROC_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_SETTLED_PROCESS_STATES = frozenset({b"X", b"Z"})
_EXECUTABLE_MODE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
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


@dataclass(frozen=True, slots=True)
class _DrainResult:
    measurement: CaptureMeasurement
    write_error: OSError | None
    truncated: bool = False


@dataclass(slots=True)
class OwnedProcessGroup:
    """One child leader and every descendant that remains in its process group."""

    process: subprocess.Popen[bytes]
    pgid: int
    _previous_handlers: dict[signal.Signals, Any] = field(default_factory=dict)
    _terminal_fd: int | None = None
    _previous_foreground_pgid: int | None = None
    _restored: bool = False
    _handlers_restored: bool = False
    _reaping_started: bool = False

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
        return _poll_leader_without_reaping(self.process)

    def terminate(self) -> None:
        self.signal_group(signal.SIGTERM)

    def kill(self) -> None:
        self.signal_group(signal.SIGKILL)

    def signal_group(self, signum: signal.Signals) -> None:
        if self._reaping_started or self.process.returncode is not None:
            raise OwnedProcessError("owned process group authority ended before signal")
        try:
            anchored = self.process.pid == self.pgid and os.getpgid(self.pid) == self.pgid
        except OSError as exc:
            raise OwnedProcessError("owned process group leader cannot be verified") from exc
        if not anchored:
            raise OwnedProcessError("owned process group leader no longer anchors its PGID")
        _signal_process_group(self.pgid, signum)

    def wait(self) -> int:
        """Wait for the leader, settle remaining group members, and restore state."""

        failures: list[BaseException] = []
        try:
            _wait_for_leader_exit_without_reaping(self.process, timeout_seconds=None)
        except BaseException as exc:
            logger.error("owned_process_wait_failed", exc_info=True)
            failures.append(exc)
        returncode = self._settle_reap_and_verify(
            failures,
            reap_timeout_seconds=None,
        )
        try:
            self._restore_parent_state()
        except BaseException as exc:
            logger.error("owned_process_parent_restore_failed", exc_info=True)
            failures.append(exc)

        if failures:
            if len(failures) == 1:
                raise failures[0]
            raise BaseExceptionGroup("owned process wait failed", failures)
        if returncode is None:
            raise OwnedProcessError("owned process leader has no return code")
        return returncode

    def settle(self) -> int:
        """Terminate the group when necessary, settle it, then reap the leader."""

        failures: list[BaseException] = []
        try:
            if _poll_leader_without_reaping(self.process) is None:
                self.signal_group(signal.SIGTERM)
                if not _wait_for_leader_exit_without_reaping(
                    self.process,
                    timeout_seconds=_TERM_TIMEOUT_SECONDS,
                ):
                    self.signal_group(signal.SIGKILL)
                    if not _wait_for_leader_exit_without_reaping(
                        self.process,
                        timeout_seconds=_KILL_TIMEOUT_SECONDS,
                    ):
                        failures.append(
                            OwnedProcessError(
                                f"owned process leader {self.process.pid} did not exit"
                            )
                        )
        except BaseException as exc:
            logger.error("owned_process_termination_failed", exc_info=True)
            failures.append(exc)

        returncode = self._settle_reap_and_verify(
            failures,
            reap_timeout_seconds=_KILL_TIMEOUT_SECONDS,
        )
        try:
            self._restore_parent_state()
        except BaseException as exc:
            logger.error("owned_process_parent_restore_failed", exc_info=True)
            failures.append(exc)

        if failures:
            if len(failures) == 1:
                raise failures[0]
            raise BaseExceptionGroup("owned process cleanup failed", failures)
        if returncode is None:
            raise OwnedProcessError("owned process leader has no return code")
        return returncode

    def _settle_reap_and_verify(
        self,
        failures: list[BaseException],
        *,
        reap_timeout_seconds: float | None,
    ) -> int | None:
        """Settle under the anchored PGID, then reap and verify absence."""

        returncode = self.process.returncode
        if returncode is None:
            try:
                self._settle_remaining_group()
            except BaseException as exc:
                logger.error("owned_process_group_cleanup_failed", exc_info=True)
                failures.append(exc)

            try:
                self._restore_signal_handlers()
                self._reaping_started = True
                if reap_timeout_seconds is None:
                    returncode = self.process.wait()
                else:
                    returncode = self.process.wait(timeout=reap_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                logger.error("owned_process_leader_reap_timed_out", exc_info=True)
                failures.append(
                    OwnedProcessError(f"owned process leader {self.process.pid} was not reaped")
                )
                failures.append(exc)
            except BaseException as exc:
                logger.error("owned_process_leader_reap_failed", exc_info=True)
                failures.append(exc)

        return returncode

    def _settle_remaining_group(self) -> None:
        remaining = _process_group_has_live_members(self.pgid)
        if remaining is False:
            return
        self.signal_group(signal.SIGTERM)
        settled = _wait_for_remaining_group_settlement(
            self.pgid,
            _TERM_TIMEOUT_SECONDS,
        )
        if settled is True:
            return
        if settled is None:
            time.sleep(_POST_EXIT_TERM_SECONDS)
            if not _process_group_exists(self.pgid):
                return

        self.signal_group(signal.SIGKILL)
        settled = _wait_for_remaining_group_settlement(
            self.pgid,
            _KILL_TIMEOUT_SECONDS,
        )
        if settled is True:
            return
        if settled is None:
            time.sleep(_POST_EXIT_KILL_SECONDS)
            return
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
        try:
            self._restore_signal_handlers()
        except BaseException as exc:
            logger.error("owned_process_signal_restore_failed", exc_info=True)
            failures.append(exc)
        if failures:
            if len(failures) == 1:
                raise failures[0]
            raise BaseExceptionGroup("parent process state restoration failed", failures)

    def _restore_signal_handlers(self) -> None:
        if self._handlers_restored:
            return
        self._handlers_restored = True
        failures: list[BaseException] = []
        for signum, previous in self._previous_handlers.items():
            try:
                signal.signal(signum, previous)  # noqa: TID251
            except BaseException as exc:
                logger.error("owned_process_signal_restore_failed", exc_info=True)
                failures.append(exc)
        if failures:
            if len(failures) == 1:
                raise failures[0]
            raise BaseExceptionGroup("parent signal restoration failed", failures)


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
            if process.returncode is None and os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            with contextlib.suppress(OSError, ProcessLookupError):
                process.kill()
        try:
            process.wait(timeout=_KILL_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError):
            pass
        raise OwnedProcessError("cannot restore runner cwd") from restore_error
    return _finish_owned_spawn(process, inherit_terminal=not capture_output)


def _finish_owned_spawn(
    process: subprocess.Popen[bytes],
    *,
    inherit_terminal: bool,
) -> OwnedProcessGroup:
    """Finish ownership setup for a process atomically spawned by this module."""

    pgid = process.pid
    try:
        valid_leader = pgid > 1 and process.returncode is None and os.getpgid(pgid) == pgid
    except OSError:
        valid_leader = False
    if not valid_leader:
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


def _wrap_user_command(command: str) -> str:
    separator = "" if command.endswith("\n") else "\n"
    return f"(\ntrap '__as_user_ec=$?; wait; exit \"$__as_user_ec\"' EXIT\n{command}{separator})"


def _scrubbed_user_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in PROTECTED_CAPTURE_ENV_VARS:
        environment.pop(name, None)
    return environment


def _spawn_bash(
    bash_path: str,
    command: str,
    *,
    capture_output: bool,
) -> subprocess.Popen[bytes]:
    try:
        inherited_cwd_fd = os.open(
            ".",
            _DIRECTORY_FLAGS & ~getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise CaptureSetupError.from_os_error(exc, "cannot preserve runner cwd") from exc

    process: subprocess.Popen[bytes] | None = None
    restore_error: OSError | None = None
    try:
        os.fchdir(inherited_cwd_fd)
        process = subprocess.Popen(
            [bash_path, "-c", _wrap_user_command(command)],
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.STDOUT if capture_output else None,
            close_fds=True,
            env=_scrubbed_user_environment(),
            process_group=0,
            start_new_session=False,
        )
    except OSError as exc:
        if exc.errno == errno.E2BIG:
            raise CaptureSetupError.from_os_error(
                exc,
                "capture shell spawn rejected: argument/environment exceeds system limit",
            ) from exc
        raise CaptureSetupError.from_os_error(exc, "cannot spawn capture shell") from exc
    finally:
        try:
            os.fchdir(inherited_cwd_fd)
        except OSError as exc:
            restore_error = exc
        os.close(inherited_cwd_fd)

    if restore_error is not None:
        if process is not None:
            _settle_failed_capture(_own_spawned_process(process, capture_output=capture_output))
        raise CaptureSetupError.from_os_error(
            restore_error, "cannot restore runner cwd"
        ) from restore_error
    if process is None:
        raise CaptureSetupError.unknown("capture shell did not start")
    return process


def _drain_capture(
    process: subprocess.Popen[bytes] | OwnedProcessGroup,
    artifact_writer_fd: int,
    inline_bytes: int,
    *,
    digest_factory: Callable[[], Any],
    write_all: Callable[[int, bytes], None],
) -> _DrainResult:
    """Read the combined subprocess pipe and persist bounded replay metadata."""

    stream = process.stdout
    if stream is None:
        raise CaptureSetupError.filesystem_io("capture pipe unavailable")

    head_limit = (2 * inline_bytes) // 3
    tail_limit = inline_bytes - head_limit
    total = 0
    digest = digest_factory()
    inline = bytearray()
    head = bytearray()
    tail = bytearray()
    write_error: OSError | None = None

    def consume(chunk: bytes) -> None:
        nonlocal total, write_error
        total += len(chunk)
        digest.update(chunk)
        if write_error is None:
            try:
                write_all(artifact_writer_fd, chunk)
            except OSError as exc:
                write_error = exc
        if len(inline) <= inline_bytes:
            remaining = inline_bytes + 1 - len(inline)
            inline.extend(chunk[:remaining])
        if len(head) < head_limit:
            head.extend(chunk[: head_limit - len(head)])
        if tail_limit:
            tail.extend(chunk)
            if len(tail) > tail_limit:
                del tail[:-tail_limit]

    def result(*, truncated: bool) -> _DrainResult:
        return _DrainResult(
            measurement=CaptureMeasurement(
                total_bytes=total,
                sha256=digest.hexdigest(),
                inline_bytes=inline_bytes,
                inline=bytes(inline),
                head=bytes(head),
                tail=bytes(tail),
            ),
            write_error=write_error,
            truncated=truncated,
        )

    if not isinstance(process, OwnedProcessGroup):
        while True:
            chunk = stream.read(_DRAIN_CHUNK_BYTES)
            if not chunk:
                return result(truncated=False)
            consume(chunk)

    descriptor = stream.fileno()
    os.set_blocking(descriptor, False)
    selector_factory = selectors.DefaultSelector
    selector = selector_factory()
    selector.register(descriptor, selectors.EVENT_READ)
    leader_exit_at: float | None = None
    kill_sent_at: float | None = None
    try:
        while True:
            for _key, _events in selector.select(_DRAIN_POLL_SECONDS):
                while True:
                    try:
                        chunk = os.read(descriptor, _DRAIN_CHUNK_BYTES)
                    except BlockingIOError:
                        break
                    if not chunk:
                        selector.unregister(descriptor)
                        return result(truncated=False)
                    consume(chunk)

            if process.poll() is None:
                continue
            now = time.monotonic()
            if leader_exit_at is None:
                leader_exit_at = now
                process.signal_group(signal.SIGTERM)
                continue
            if kill_sent_at is None and now - leader_exit_at >= _POST_EXIT_TERM_SECONDS:
                kill_sent_at = now
                process.signal_group(signal.SIGKILL)
                continue
            if kill_sent_at is not None and now - kill_sent_at >= _POST_EXIT_KILL_SECONDS:
                return result(truncated=True)
    finally:
        selector.close()


def _normalized_returncode(returncode: int) -> int:
    return 128 + (-returncode) if returncode < 0 else returncode


def _resolve_bash(candidates: Sequence[str] = _TRUSTED_BASH_CANDIDATES) -> str:
    for candidate in candidates:
        if not os.path.isabs(candidate):
            continue
        try:
            fd = os.open(candidate, _READ_FLAGS)
        except OSError:
            continue
        try:
            value = os.fstat(fd)
            if (
                stat.S_ISREG(value.st_mode)
                and value.st_uid == 0
                and value.st_mode & _EXECUTABLE_MODE_BITS
                and not value.st_mode & _UNTRUSTED_WRITE_BITS
            ):
                return candidate
        except OSError:
            pass
        finally:
            os.close(fd)
    raise CaptureSetupError.authority("trusted bash executable unavailable")


def _settle_failed_capture(
    process: subprocess.Popen[bytes] | OwnedProcessGroup,
) -> _capture_replay.RunnerSettlementEvidence:
    if not isinstance(process, OwnedProcessGroup):
        return _capture_replay.settle_failed_capture(process)
    try:
        return _capture_replay.RunnerSettlementEvidence(
            action="settled_owned_group",
            returncode=process.settle(),
        )
    except BaseException:
        logger.error("owned_capture_settlement_failed", exc_info=True)
        return _capture_replay.RunnerSettlementEvidence(
            action="owned_group_settlement_failed",
            returncode=None,
        )


def _own_spawned_process(
    process: subprocess.Popen[bytes],
    *,
    capture_output: bool,
) -> subprocess.Popen[bytes] | OwnedProcessGroup:
    """Adopt real subprocesses while retaining narrow injected test doubles."""

    pid = getattr(process, "pid", None)
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        return process
    return _finish_owned_spawn(process, inherit_terminal=not capture_output)


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


def _poll_leader_without_reaping(
    process: subprocess.Popen[bytes],
) -> int | None:
    """Observe a real child leader without releasing its PGID anchor."""

    if process.returncode is not None:
        return process.returncode
    if not isinstance(process, subprocess.Popen):
        return process.poll()
    required = ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "waitid")
    if any(not hasattr(os, name) for name in required):
        raise OwnedProcessError("non-reaping process observation is unavailable")
    try:
        result = os.waitid(
            os.P_PID,
            process.pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except ChildProcessError as exc:
        raise OwnedProcessError(f"owned process leader {process.pid} is not waitable") from exc
    if result is None:
        return None
    if result.si_code == os.CLD_EXITED:
        return int(result.si_status)
    return -int(result.si_status)


def _wait_for_leader_exit_without_reaping(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float | None,
) -> bool:
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while _poll_leader_without_reaping(process) is None:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(_GROUP_POLL_SECONDS, remaining))
        else:
            time.sleep(_GROUP_POLL_SECONDS)
    return True


def _wait_for_remaining_group_settlement(
    pgid: int,
    timeout_seconds: float,
) -> bool | None:
    """Wait until no live member remains besides settled zombies."""

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = _process_group_has_live_members(pgid)
        if remaining is not True:
            return None if remaining is None else True
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return False
        time.sleep(min(_GROUP_POLL_SECONDS, remaining_seconds))


def _process_group_has_live_members(pgid: int) -> bool | None:
    """Return live-member state, or None when the proc view is unavailable."""

    group_exists, liveness_visible = _probe_process_group(pgid)
    if not group_exists:
        return False
    try:
        entries = os.scandir(_PROC_ROOT)
    except OSError:
        return None

    indeterminate = False
    with entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                descriptor = os.open(entry.path + "/stat", _PROC_READ_FLAGS)
            except FileNotFoundError:
                continue
            except OSError:
                indeterminate = True
                continue
            try:
                raw_stat = os.read(descriptor, _PROC_STAT_READ_BYTES)
            except OSError:
                indeterminate = True
                continue
            finally:
                os.close(descriptor)

            parsed = _parse_proc_stat_group_and_state(raw_stat)
            if parsed is None:
                indeterminate = True
                continue
            member_pgid, member_state = parsed
            if member_pgid == pgid and member_state not in _SETTLED_PROCESS_STATES:
                return True
    return None if indeterminate or not liveness_visible else False


def _parse_proc_stat_group_and_state(raw_stat: bytes) -> tuple[int, bytes] | None:
    command_end = raw_stat.rfind(b")")
    if command_end < 0:
        return None
    fields = raw_stat[command_end + 2 :].split()
    if len(fields) < 3:
        return None
    try:
        return int(fields[2]), fields[0]
    except ValueError:
        return None


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
    return _probe_process_group(pgid)[0]


def _probe_process_group(pgid: int) -> tuple[bool, bool]:
    """Return existence and whether signal-zero liveness was observable."""

    if pgid <= 1:
        raise OwnedProcessError("unsafe owned process group identity")
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False, True
    except PermissionError:
        return True, False
    return True, True


def _signal_process_group(pgid: int, signum: signal.Signals) -> None:
    if pgid <= 1:
        raise OwnedProcessError("unsafe owned process group identity")
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return


__all__ = [
    "OwnedProcessError",
    "OwnedProcessGroup",
    "spawn_owned_process",
]
