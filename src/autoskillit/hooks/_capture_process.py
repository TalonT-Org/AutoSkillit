"""Owned POSIX process groups for the isolated shell runner.

The helper is intentionally stdlib-only. It creates the child group atomically
beneath a runner-owned lifeline anchor, forwards host signals without treating
them as runner settlement, restores inherited terminal state, and does not
return until the leader and owned group are settled. Closing the runner's
lifeline makes the anchor SIGKILL its whole group, including when the runner
itself is killed.
"""

from __future__ import annotations

import logging
import os
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _replay as _capture_replay
    from autoskillit.hooks._capture._authority import (
        _READ_FLAGS,
        _UNTRUSTED_WRITE_BITS,
        CaptureSetupError,
    )
elif __package__:
    from ._capture import _replay as _capture_replay
    from ._capture._authority import (
        _READ_FLAGS,
        _UNTRUSTED_WRITE_BITS,
        CaptureSetupError,
    )
else:
    from _capture import _replay as _capture_replay
    from _capture._authority import (
        _READ_FLAGS,
        _UNTRUSTED_WRITE_BITS,
        CaptureSetupError,
    )

_TERM_TIMEOUT_SECONDS = 2.0
_KILL_TIMEOUT_SECONDS = 2.0
_GROUP_POLL_SECONDS = 0.02
_POST_EXIT_TERM_SECONDS = 0.25
_POST_EXIT_KILL_SECONDS = 0.5
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
_OWNED_PROCESS_SPAWN_TOKEN = object()


class OwnedProcessError(RuntimeError):
    """The runner could not prove complete process-group settlement.

    NOTE: ``autoskillit.execution.process._lifecycle.owned_group.OwnedProcessStoppedError``
    is the dedicated subclass for the stopped-leader condition in the
    lifecycle layer. The hooks instance shares the same ``leader_pid``,
    ``pgid``, and ``stop_signal`` payload (populated at the stopped-leader
    raise site) so downstream ``except`` clauses that read these fields work
    identically across both hierarchies. Unification is intentionally
    avoided: hooks is contractually stdlib-only.
    """

    def __init__(
        self,
        message: str,
        *,
        leader_pid: int | None = None,
        pgid: int | None = None,
        stop_signal: int | None = None,
    ) -> None:
        super().__init__(message)
        self.leader_pid = leader_pid
        self.pgid = pgid
        self.stop_signal = stop_signal


class SignalOrigin(StrEnum):
    RUNNER = "runner"
    FORWARDED = "forwarded"


def _add_cleanup_failure_note(
    primary_error: BaseException,
    context: str,
    cleanup_error: BaseException,
) -> None:
    detail = str(cleanup_error)[:256]
    primary_error.add_note(f"{context}: {type(cleanup_error).__name__}: {detail}")


@dataclass(slots=True)
class OwnedProcessGroup:
    """One child leader and every descendant beneath its lifeline anchor."""

    process: subprocess.Popen[bytes]
    pgid: int
    anchor: subprocess.Popen[bytes]
    _lifeline_fd: int = -1
    _lifeline_closed: bool = False
    _previous_handlers: dict[signal.Signals, Any] = field(default_factory=dict)
    _terminal_fd: int | None = None
    _previous_foreground_pgid: int | None = None
    _restored: bool = False
    _handlers_restored: bool = False
    _reaping_started: bool = False
    _runner_signals: list[signal.Signals] = field(default_factory=list)
    _spawn_token: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._spawn_token is not _OWNED_PROCESS_SPAWN_TOKEN:
            raise TypeError("OwnedProcessGroup instances must come from a spawn helper")

    @property
    def stdout(self) -> IO[bytes] | None:
        return self.process.stdout

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def anchor_pid(self) -> int:
        return self.anchor.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    @property
    def runner_signalled(self) -> bool:
        return bool(self._runner_signals)

    def poll(self) -> int | None:
        return _poll_leader_without_reaping(self.process, self.pgid)

    def terminate(self) -> None:
        self.signal_group(signal.SIGTERM, origin=SignalOrigin.RUNNER)

    def kill(self) -> None:
        self.signal_group(signal.SIGKILL, origin=SignalOrigin.RUNNER)

    def signal_group(self, signum: signal.Signals, *, origin: SignalOrigin) -> None:
        if self._reaping_started or self.anchor.returncode is not None:
            raise OwnedProcessError("owned process group authority ended before signal")
        try:
            anchored = self.anchor.pid == self.pgid and os.getpgid(self.anchor.pid) == self.pgid
        except OSError as exc:
            raise OwnedProcessError("owned process group anchor cannot be verified") from exc
        if not anchored:
            raise OwnedProcessError("owned process group anchor no longer anchors its PGID")
        if origin is SignalOrigin.RUNNER:
            self._runner_signals.append(signum)
        _signal_process_group(self.pgid, signum)

    def wait(self) -> int:
        """Wait for the leader, settle remaining group members, and restore state."""

        failures: list[BaseException] = []
        try:
            _wait_for_leader_exit_without_reaping(self.process, self.pgid, timeout_seconds=None)
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
            if (
                _poll_leader_without_reaping(self.process, self.pgid, include_stopped=False)
                is None
            ):
                self.signal_group(signal.SIGTERM, origin=SignalOrigin.RUNNER)
                if not _wait_for_leader_exit_without_reaping(
                    self.process,
                    self.pgid,
                    timeout_seconds=_TERM_TIMEOUT_SECONDS,
                    include_stopped=False,
                ):
                    self.signal_group(signal.SIGKILL, origin=SignalOrigin.RUNNER)
                    if not _wait_for_leader_exit_without_reaping(
                        self.process,
                        self.pgid,
                        timeout_seconds=_KILL_TIMEOUT_SECONDS,
                        include_stopped=False,
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

        if self.anchor.returncode is None:
            try:
                self._settle_remaining_group()
            except BaseException as exc:
                logger.error("owned_process_group_cleanup_failed", exc_info=True)
                failures.append(exc)

        try:
            self._restore_signal_handlers()
        except BaseException as exc:
            logger.error("owned_process_signal_restore_failed", exc_info=True)
            failures.append(exc)

        if self.anchor.returncode is None:
            try:
                self._release_lifeline()
            except BaseException as exc:
                logger.error("owned_process_lifeline_release_failed", exc_info=True)
                failures.append(exc)

        if self.process.returncode is None:
            try:
                if reap_timeout_seconds is None:
                    self.process.wait()
                else:
                    self.process.wait(timeout=reap_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                logger.error("owned_process_leader_reap_timed_out", exc_info=True)
                failures.append(
                    OwnedProcessError(f"owned process leader {self.process.pid} was not reaped")
                )
                failures.append(exc)
            except BaseException as exc:
                logger.error("owned_process_leader_reap_failed", exc_info=True)
                failures.append(exc)

        return self.process.returncode

    def _settle_remaining_group(self) -> None:
        remaining = _process_group_has_live_members(self.pgid, ignore_pid=self.anchor.pid)
        if remaining is False:
            return
        self.signal_group(signal.SIGTERM, origin=SignalOrigin.RUNNER)
        settled = _wait_for_remaining_group_settlement(
            self.pgid,
            _TERM_TIMEOUT_SECONDS,
            ignore_pid=self.anchor.pid,
        )
        if settled is None:
            time.sleep(_POST_EXIT_TERM_SECONDS)

    def _release_lifeline(self) -> None:
        if not self._lifeline_closed:
            self._lifeline_closed = True
            lifeline_fd = self._lifeline_fd
            try:
                if lifeline_fd >= 0:
                    os.close(lifeline_fd)
            finally:
                self._lifeline_fd = -1

        if self.anchor.returncode is not None:
            return

        anchor_exited = _wait_for_leader_exit_without_reaping(
            self.anchor,
            self.pgid,
            timeout_seconds=_KILL_TIMEOUT_SECONDS,
            include_stopped=False,
        )
        if not anchor_exited:
            self.signal_group(signal.SIGKILL, origin=SignalOrigin.RUNNER)
            anchor_exited = _wait_for_leader_exit_without_reaping(
                self.anchor,
                self.pgid,
                timeout_seconds=_KILL_TIMEOUT_SECONDS,
                include_stopped=False,
            )
            if not anchor_exited:
                raise OwnedProcessError(
                    f"owned process group anchor {self.anchor.pid} did not exit"
                )

        settled = _wait_for_remaining_group_settlement(
            self.pgid,
            _KILL_TIMEOUT_SECONDS,
        )
        if settled is None:
            time.sleep(_POST_EXIT_KILL_SECONDS)
        elif not settled:
            self.signal_group(signal.SIGKILL, origin=SignalOrigin.RUNNER)
            settled = _wait_for_remaining_group_settlement(
                self.pgid,
                _KILL_TIMEOUT_SECONDS,
            )
            if settled is False:
                raise OwnedProcessError(
                    f"owned process group {self.pgid} survived lifeline release"
                )
            if settled is None:
                time.sleep(_POST_EXIT_KILL_SECONDS)

        self._reaping_started = True
        self.anchor.wait(timeout=_KILL_TIMEOUT_SECONDS)

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


if TYPE_CHECKING:
    from autoskillit.hooks import _capture_spawn
elif __package__:
    from . import _capture_spawn
else:
    import _capture_spawn

_TRUSTED_BASH_CANDIDATES = _capture_spawn._TRUSTED_BASH_CANDIDATES
spawn_owned_process = _capture_spawn.spawn_owned_process
_finish_owned_spawn = _capture_spawn._finish_owned_spawn
_scrubbed_user_environment = _capture_spawn._scrubbed_user_environment
_spawn_bash = _capture_spawn._spawn_bash


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
    *,
    primary_error: BaseException | None = None,
) -> _capture_replay.RunnerSettlementEvidence:
    if not isinstance(process, OwnedProcessGroup):
        return _capture_replay.settle_failed_capture(process)
    try:
        return _capture_replay.RunnerSettlementEvidence(
            action="settled_owned_group",
            returncode=process.settle(),
        )
    except BaseException as cleanup_error:
        logger.error("owned_capture_settlement_failed", exc_info=True)
        if primary_error is not None:
            _add_cleanup_failure_note(
                primary_error,
                "owned capture settlement also failed",
                cleanup_error,
            )
        return _capture_replay.RunnerSettlementEvidence(
            action="owned_group_settlement_failed",
            returncode=None,
        )


def _install_signal_forwarding(
    owner: OwnedProcessGroup,
) -> dict[signal.Signals, Any]:
    previous: dict[signal.Signals, Any] = {}

    def forward(signum: int, _frame: object) -> None:
        try:
            owner.signal_group(
                signal.Signals(signum),
                origin=SignalOrigin.FORWARDED,
            )
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
    try:
        _safe_tcsetpgrp(terminal_fd, pgid)
        try:
            os.killpg(pgid, signal.SIGCONT)
        except ProcessLookupError:
            logger.debug("owned_process_foreground_cont_lookup_raced", extra={"pgid": pgid})
    except BaseException as primary_error:
        try:
            _safe_tcsetpgrp(terminal_fd, previous_pgid)
        except BaseException as restore_error:
            logger.error("owned_process_foreground_restore_failed", exc_info=True)
            _add_cleanup_failure_note(
                primary_error,
                "foreground process-group restoration also failed",
                restore_error,
            )
        raise
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
    pgid: int,
    *,
    include_stopped: bool = True,
) -> int | None:
    """Observe a real child leader without releasing its PGID anchor.

    ``pgid`` is the leader's process-group id captured at spawn time. It is
    reported verbatim when a stopped observation surfaces so downstream
    handlers can reconcile the leader against its PGID anchor; it is not
    recovered from ``process.pid`` because the leader's pid alone may not
    match a fresh ``os.getpgid`` lookup if the group has been reaped.
    """

    if process.returncode is not None:
        return process.returncode
    if not isinstance(process, subprocess.Popen):
        return process.poll()
    required = ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "waitid")
    if any(not hasattr(os, name) for name in required):
        raise OwnedProcessError("non-reaping process observation is unavailable")
    wait_flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
    stopped_observation = include_stopped and all(
        hasattr(os, name) for name in ("WSTOPPED", "CLD_STOPPED")
    )
    if stopped_observation:
        wait_flags |= os.WSTOPPED
    if sys.platform == "darwin" and sys.version_info < (3, 13):
        raise OwnedProcessError("non-reaping process observation is unavailable")
    try:
        result = os.waitid(
            os.P_PID,
            process.pid,
            wait_flags,
        )
    except ChildProcessError as exc:
        raise OwnedProcessError(f"owned process leader {process.pid} is not waitable") from exc
    if result is None:
        return None
    if stopped_observation and result.si_code == os.CLD_STOPPED:
        raise OwnedProcessError(
            f"owned process leader {process.pid} in group {pgid} "
            f"stopped by signal {result.si_status}",
            leader_pid=process.pid,
            pgid=pgid,
            stop_signal=int(result.si_status),
        )
    if result.si_code == os.CLD_EXITED:
        return int(result.si_status)
    return -int(result.si_status)


def _wait_for_leader_exit_without_reaping(
    process: subprocess.Popen[bytes],
    pgid: int,
    *,
    timeout_seconds: float | None,
    include_stopped: bool = True,
) -> bool:
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while _poll_leader_without_reaping(process, pgid, include_stopped=include_stopped) is None:
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
    *,
    ignore_pid: int | None = None,
) -> bool | None:
    """Wait until no live member remains besides settled zombies."""

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = _process_group_has_live_members(pgid, ignore_pid=ignore_pid)
        if remaining is not True:
            return None if remaining is None else True
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return False
        time.sleep(min(_GROUP_POLL_SECONDS, remaining_seconds))


def _process_group_has_live_members(
    pgid: int,
    *,
    ignore_pid: int | None = None,
) -> bool | None:
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
            if not entry.name.isdecimal() or entry.name == str(ignore_pid):
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
    "SignalOrigin",
    "spawn_owned_process",
]

if __package__:
    from ._capture import _module_identity
else:
    from _capture import _module_identity as _standalone_module_identity

    _module_identity = _standalone_module_identity
_module_identity.register_module_aliases(__name__)
