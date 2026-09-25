"""POSIX process ownership for one interactive cook attempt."""

from __future__ import annotations

import contextlib
import math
import os
import signal
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO
from uuid import uuid4

from autoskillit.cli.session._session_startup_trace import StartupTrace
from autoskillit.cli.session.pty._exec import launcher_argv
from autoskillit.cli.session.pty._observer import PtyObserver
from autoskillit.cli.ui._terminal import terminal_guard, terminal_last_activity
from autoskillit.config import ProcessTetherConfig
from autoskillit.core import (
    SESSION_LIFETIME_NOTICE_ENV_VAR,
    CmdSpec,
    ProcessCleanupResult,
    TerminationReason,
    ensure_project_temp,
    get_logger,
    write_versioned_json,
)
from autoskillit.execution import (
    TETHER_LEASE_RENEW_SECONDS,
    TETHER_LEASE_SECONDS,
    OwnedProcessGroup,
    TetherSpec,
    _active_liveness_signals,
    renew_tether,
    spawn_owned_process,
    wrap_systemd_scope,
)

_GROUP_POLL_SECONDS = 0.02
LIFETIME_EXIT_STATUS: Final = 124
logger = get_logger(__name__)


OWNER_PRECEDENCE_MARGIN_SECONDS: Final = 600.0
INTERACTIVE_TERMINATION_GRACE_SECONDS: Final = 10.0
_LIVENESS_PROBE_INTERVAL_SECONDS: Final = 60.0
_IDLE_WINDOW_SECONDS: Final = 1800.0
_LEASE_RETRY_SECONDS: Final = 60.0
_WARNING_LEADS: Final[tuple[tuple[float, str], ...]] = (
    (_IDLE_WINDOW_SECONDS, "30m"),
    (300.0, "5m"),
)


def _default_activity(pid: int, fd: int | None) -> frozenset[str]:
    """Read process liveness and recent kernel TTY activity."""
    signals = set(_active_liveness_signals(pid, None, None))
    if fd is not None:
        last_activity = terminal_last_activity(fd)
        if last_activity is not None and time.time() - last_activity <= _IDLE_WINDOW_SECONDS:
            signals.add("terminal_io")
    return frozenset(signals)


class InteractiveLifetime:
    """Decide when an interactive session has exhausted its lifetime."""

    def __init__(
        self,
        policy: ProcessTetherConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        activity_probe: Callable[[int, int | None], frozenset[str]] | None = None,
    ) -> None:
        policy.validate()
        soft_seconds = policy.cook_ceiling_seconds
        extension_seconds = policy.cook_max_extension_seconds
        hard_cap_seconds = soft_seconds + extension_seconds
        scope_runtime_max_seconds = hard_cap_seconds + OWNER_PRECEDENCE_MARGIN_SECONDS
        if not math.isfinite(hard_cap_seconds) or not math.isfinite(scope_runtime_max_seconds):
            raise ValueError("combined interactive lifetime limits must be finite")

        self.scope_runtime_max_seconds = scope_runtime_max_seconds
        self.tether_ceiling_seconds = min(TETHER_LEASE_SECONDS, scope_runtime_max_seconds)
        self.systemd_scope_enabled = policy.systemd_scope_enabled

        self._soft_seconds = soft_seconds
        self._hard_cap_seconds = hard_cap_seconds
        self._clock = clock
        self._wall = wall
        self._activity_probe = _default_activity if activity_probe is None else activity_probe
        self._started_at: float | None = None
        self._soft_deadline: float | None = None
        self._hard_deadline: float | None = None
        self._next_probe_at: float | None = None
        self._last_active_mono: float | None = None
        self._probes_after_soft = 0
        self._last_signals: frozenset[str] = frozenset()
        self._decision: TerminationReason | None = None
        self._warning_levels_written: set[str] = set()
        self._lease_renewal_failure_logged = False
        self._pid: int | None = None
        self._tether_path: Path | None = None
        self._terminal_fd: int | None = None
        self._notice_path: Path | None = None
        self._lease_due_wall: float | None = None

    @property
    def decision(self) -> TerminationReason | None:
        return self._decision

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, self._clock() - self._started_at)

    def start(
        self,
        *,
        pid: int,
        tether_path: Path | None,
        terminal_fd: int | None,
        notice_path: Path | None,
    ) -> None:
        """Start lifetime accounting using clock reads only."""
        now = self._clock()
        wall_now = self._wall()
        self._started_at = now
        self._soft_deadline = now + self._soft_seconds
        self._hard_deadline = now + self._hard_cap_seconds
        self._next_probe_at = self._soft_deadline
        self._last_active_mono = now
        self._probes_after_soft = 0
        self._last_signals = frozenset()
        self._decision = None
        self._warning_levels_written.clear()
        self._lease_renewal_failure_logged = False
        self._pid = pid
        self._tether_path = tether_path
        self._terminal_fd = terminal_fd
        self._notice_path = notice_path
        self._lease_due_wall = wall_now + TETHER_LEASE_RENEW_SECONDS

    def poll(self) -> TerminationReason | None:
        """Renew the lease and return a sticky lifetime decision when reached."""
        if self._started_at is None:
            return None
        if self._decision is not None:
            return self._decision

        now = self._clock()
        wall_now = self._wall()
        self._renew_lease(now, wall_now)

        assert self._hard_deadline is not None
        if now >= self._hard_deadline:
            return self._decide(TerminationReason.TIMED_OUT, now)

        self._write_due_warnings(now, wall_now)
        return self._poll_activity(now)

    def _poll_activity(self, now: float) -> TerminationReason | None:
        assert self._started_at is not None
        assert self._soft_deadline is not None
        if now < self._soft_deadline:
            return None
        assert self._next_probe_at is not None
        if now < self._next_probe_at:
            return None

        self._next_probe_at = now + _LIVENESS_PROBE_INTERVAL_SECONDS
        first_probe = self._probes_after_soft == 0
        self._probes_after_soft += 1
        assert self._pid is not None
        try:
            signals = frozenset(self._activity_probe(self._pid, self._terminal_fd))
        except Exception:
            # Probe failure is an unknown state, not evidence of activity.
            # Leave _last_active_mono untouched so the IDLE_STALL backstop
            # can still fire if probes keep failing.
            self._last_signals = frozenset()
            logger.warning(
                "cook_lifetime_probe_failed",
                pid=self._pid,
                elapsed_seconds=now - self._started_at,
                exc_info=True,
            )
            return None

        return self._apply_activity_signals(now, first_probe, signals)

    def _apply_activity_signals(
        self,
        now: float,
        first_probe: bool,
        signals: frozenset[str],
    ) -> TerminationReason | None:
        assert self._started_at is not None
        self._last_signals = signals
        if any(signal != "terminal_io" for signal in signals):
            self._last_active_mono = now
        if signals:
            logger.info(
                "cook_lifetime_extended",
                signals=sorted(signals),
                elapsed_seconds=now - self._started_at,
                hard_cap_in_seconds=self._hard_cap_seconds,
            )
            return None

        if not first_probe:
            assert self._last_active_mono is not None
            if now - self._last_active_mono >= _IDLE_WINDOW_SECONDS:
                return self._decide(TerminationReason.IDLE_STALL, now)
        return None

    def _renew_lease(self, now: float, wall_now: float) -> None:
        assert self._lease_due_wall is not None
        if wall_now < self._lease_due_wall:
            return

        if self._tether_path is None:
            self._lease_due_wall = wall_now + TETHER_LEASE_RENEW_SECONDS
            return

        assert self._hard_deadline is not None
        hard_remaining = self._hard_deadline - now
        not_after = wall_now + min(
            TETHER_LEASE_SECONDS,
            hard_remaining + OWNER_PRECEDENCE_MARGIN_SECONDS,
        )
        try:
            renewed = renew_tether(self._tether_path, not_after)
        except Exception:
            renewed = False
            if not self._lease_renewal_failure_logged:
                logger.warning(
                    "cook_tether_lease_renew_failed",
                    path=str(self._tether_path),
                    not_after=not_after,
                    exc_info=True,
                )
                self._lease_renewal_failure_logged = True
        if renewed:
            self._lease_due_wall = wall_now + TETHER_LEASE_RENEW_SECONDS
            self._lease_renewal_failure_logged = False
            return

        if not self._lease_renewal_failure_logged:
            logger.warning(
                "cook_tether_lease_renew_failed",
                path=str(self._tether_path),
                not_after=not_after,
            )
            self._lease_renewal_failure_logged = True
        self._lease_due_wall = wall_now + _LEASE_RETRY_SECONDS

    def _write_due_warnings(self, now: float, wall_now: float) -> None:
        if self._notice_path is None or self._hard_deadline is None or self._started_at is None:
            return
        remaining = self._hard_deadline - now
        for lead, level in _WARNING_LEADS:
            if lead >= self._hard_cap_seconds or remaining <= 0 or remaining > lead:
                continue
            if level in self._warning_levels_written:
                continue
            self._warning_levels_written.add(level)
            message = f"Interactive session has {level} left before its hard lifetime limit."
            notice = {
                "level": level,
                "deadline_epoch": wall_now + remaining,
                "message": message,
            }
            try:
                write_versioned_json(self._notice_path, notice, schema_version=1)
            except Exception:
                logger.warning(
                    "cook_lifetime_warning_write_failed",
                    path=str(self._notice_path),
                    level=level,
                    exc_info=True,
                )
                continue
            logger.info(
                "cook_lifetime_warning",
                level=level,
                deadline_epoch=notice["deadline_epoch"],
            )

    def _decide(self, reason: TerminationReason, now: float) -> TerminationReason:
        self._decision = reason
        assert self._started_at is not None
        logger.warning(
            "cook_lifetime_expired",
            reason=reason.value,
            elapsed_seconds=now - self._started_at,
            signals=sorted(self._last_signals),
        )
        return reason


@dataclass(frozen=True, slots=True)
class CookAttemptResult:
    """Result and cleanup evidence for one interactive cook attempt."""

    pid: int
    pgid: int
    returncode: int | None
    termination: TerminationReason
    elapsed_seconds: float
    cleanup: ProcessCleanupResult

    def __post_init__(self) -> None:
        if self.returncode is None and self.termination is TerminationReason.NATURAL_EXIT:
            raise ValueError("a natural cook exit must have a return code")


def _run_pre_spawn_check(spec: CmdSpec, check: Callable[[], None] | None) -> None:
    if spec.managed_skill_catalog is not None and check is None:
        raise RuntimeError("managed interactive launch requires a pre-spawn check")
    if check is not None:
        check()


def run_cook_attempt(
    spec: CmdSpec,
    *,
    pass_fds: tuple[int, ...],
    on_spawn: Callable[[int, int], None],
    on_reaped: Callable[[int, int], None],
    on_teardown_unproven: Callable[[int, int], None],
    trace: StartupTrace,
    observer: PtyObserver | None,
    lifetime: ProcessTetherConfig,
    pre_spawn_check: Callable[[], None] | None = None,
) -> CookAttemptResult:
    """Run one finalized cook command under its configured lifetime policy."""
    _require_posix_process_ownership()
    cwd = _canonical_cwd(spec.cwd)
    inherited_fds = _normalize_pass_fds(pass_fds)
    life = InteractiveLifetime(lifetime)
    terminal_fd = _interactive_terminal_fd()
    notice_path = ensure_project_temp(Path(cwd)) / "session_lifetime" / f"{uuid4().hex}.json"

    owner: OwnedProcessGroup | None = None
    pid: int | None = None
    pgid: int | None = None
    returncode: int | None = None
    cleanup_result: ProcessCleanupResult | None = None
    termination = TerminationReason.NATURAL_EXIT
    master_fd: int | None = None
    slave_fd: int | None = None
    failures: list[BaseException] = []
    deferred_master_fd: int | None = None

    with terminal_guard():
        try:
            if observer is None:
                spawn_argv: Sequence[str] = list(spec.cmd)
                spawn_fds = inherited_fds
                process_group: int | None = 0
                start_new_session = False
            else:
                master_fd, slave_fd = os.openpty()
                spawn_argv = launcher_argv(
                    slave_fd,
                    spec.cmd,
                    lease_fds=inherited_fds,
                )
                spawn_fds = _merge_launcher_fds(inherited_fds, slave_fd)
                process_group = None
                start_new_session = True
            _run_pre_spawn_check(spec, pre_spawn_check)
            # PTY mode places the launcher and its replacement workload in the same scope.
            owner = spawn_owned_process(
                wrap_systemd_scope(
                    spawn_argv,
                    enabled=life.systemd_scope_enabled,
                    ceiling_seconds=life.scope_runtime_max_seconds,
                ),
                cwd=cwd,
                env=_attempt_child_env(spec, notice_path),
                pass_fds=spawn_fds,
                process_group=process_group,
                start_new_session=start_new_session,
                tether=TetherSpec(origin="cook", ceiling_seconds=life.tether_ceiling_seconds),
            )

            pid = owner.pid
            pgid = owner.pgid
            on_spawn(pid, pgid)
            trace.record_spawn()
            life.start(
                pid=pid,
                tether_path=owner.tether_path,
                terminal_fd=terminal_fd,
                notice_path=notice_path,
            )

            if observer is None:
                with _foreground_process_group(pgid):
                    _wait_for_owned_exit(owner, life)
            else:
                assert master_fd is not None
                assert slave_fd is not None
                os.close(slave_fd)
                slave_fd = None
                relay_fd = os.dup(master_fd)
                observer.relay(
                    relay_fd,
                    cancelled=lambda: owner.observe_exit() is not None or life.poll() is not None,
                )
        except BaseException as exc:
            logger.error("cook_attempt_failed", error_type=type(exc).__name__)
            failures.append(exc)
        finally:
            deferred_master_fd = _close_attempt_fds(slave_fd, master_fd, owner, observer, failures)
            if owner is not None and pid is not None and pgid is not None:
                returncode, cleanup_result, termination = _settle_cook_owner(
                    owner,
                    pid,
                    pgid,
                    life,
                    on_reaped,
                    on_teardown_unproven,
                    failures,
                )
            if deferred_master_fd is not None:
                assert observer is not None
                try:
                    observer.close_master(deferred_master_fd)
                except BaseException as exc:
                    logger.error(
                        "cook_master_fd_close_failed",
                        error_type=type(exc).__name__,
                    )
                    failures.append(exc)
            _remove_lifetime_notice(notice_path)

    if failures:
        if len(failures) == 1:
            raise failures[0]
        raise BaseExceptionGroup("cook attempt and cleanup failed", failures)
    if (
        pid is None
        or pgid is None
        or cleanup_result is None
        or (returncode is None and life.decision is None)
    ):
        raise RuntimeError("cook attempt completed without process ownership proof")
    return CookAttemptResult(
        pid=pid,
        pgid=pgid,
        returncode=returncode,
        termination=termination,
        elapsed_seconds=life.elapsed_seconds,
        cleanup=cleanup_result,
    )


def _close_attempt_fds(
    slave_fd: int | None,
    master_fd: int | None,
    owner: OwnedProcessGroup | None,
    observer: PtyObserver | None,
    failures: list[BaseException],
) -> int | None:
    pid = owner.pid if owner is not None else None
    if slave_fd is not None:
        try:
            os.close(slave_fd)
        except BaseException as exc:
            logger.error(
                "cook_slave_fd_close_failed",
                error_type=type(exc).__name__,
                pid=pid,
                fd=slave_fd,
            )
            failures.append(exc)
    if master_fd is None:
        return None
    if owner is not None and observer is not None:
        return master_fd
    try:
        if observer is None:
            os.close(master_fd)
        else:
            observer.close_master(master_fd)
    except BaseException as exc:
        logger.error(
            "cook_master_fd_close_failed",
            error_type=type(exc).__name__,
            pid=pid,
            fd=master_fd,
        )
        failures.append(exc)
    return None


def _settle_cook_owner(
    owner: OwnedProcessGroup,
    pid: int,
    pgid: int,
    life: InteractiveLifetime,
    on_reaped: Callable[[int, int], None],
    on_teardown_unproven: Callable[[int, int], None],
    failures: list[BaseException],
) -> tuple[int | None, ProcessCleanupResult | None, TerminationReason]:
    termination = life.decision or TerminationReason.NATURAL_EXIT
    if life.decision is None:
        returncode, cleanup_result, cleanup_proved = _settle_natural_owner(
            owner, pid, pgid, failures
        )
    else:
        returncode, cleanup_result, cleanup_proved = _settle_lifetime_decision(
            owner, pid, pgid, on_teardown_unproven, failures
        )
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
    if life.decision is not None:
        logger.info(
            "cook_attempt_terminated",
            pid=pid,
            pgid=pgid,
            reason=termination.value,
            elapsed_seconds=life.elapsed_seconds,
            returncode=returncode,
            escalated=cleanup_result.escalated if cleanup_result is not None else None,
            complete=cleanup_result.complete if cleanup_result is not None else False,
        )
    return returncode, cleanup_result, termination


def _settle_lifetime_decision(
    owner: OwnedProcessGroup,
    pid: int,
    pgid: int,
    on_teardown_unproven: Callable[[int, int], None],
    failures: list[BaseException],
) -> tuple[int | None, ProcessCleanupResult | None, bool]:
    returncode: int | None = None
    cleanup_result: ProcessCleanupResult | None = None
    try:
        returncode, cleanup_result = owner.settle_evidence(
            INTERACTIVE_TERMINATION_GRACE_SECONDS, escalate=True
        )
    except BaseException as exc:
        logger.error(
            "cook_process_cleanup_failed",
            error_type=type(exc).__name__,
            pid=pid,
            pgid=pgid,
        )
        failures.append(exc)
    cleanup_proved = (
        returncode is not None and cleanup_result is not None and cleanup_result.complete
    )
    if not cleanup_proved:
        try:
            on_teardown_unproven(pid, pgid)
        except BaseException as exc:
            logger.error(
                "cook_teardown_unproven_callback_failed",
                error_type=type(exc).__name__,
                pid=pid,
                pgid=pgid,
            )
            failures.append(exc)
        if cleanup_result is not None:
            logger.error(
                "cook_lifetime_teardown_unproven",
                pid=pid,
                pgid=pgid,
                evidence=cleanup_result.to_dict(),
            )
    return returncode, cleanup_result, cleanup_proved


def _settle_natural_owner(
    owner: OwnedProcessGroup,
    pid: int,
    pgid: int,
    failures: list[BaseException],
) -> tuple[int | None, ProcessCleanupResult | None, bool]:
    try:
        if failures:
            cleanup_result = owner.settle_preserving(failures[0])
            returncode = owner.process.returncode
            return (
                returncode,
                cleanup_result,
                returncode is not None and cleanup_result.complete,
            )
        returncode, cleanup_result = owner.settle()
        return returncode, cleanup_result, cleanup_result.complete
    except BaseException as exc:
        logger.error(
            "cook_process_cleanup_failed",
            error_type=type(exc).__name__,
            pid=pid,
            pgid=pgid,
        )
        failures.append(exc)
        return None, None, False


def _remove_lifetime_notice(notice_path: Path) -> None:
    try:
        notice_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "cook_lifetime_notice_cleanup_failed",
            error_type=type(exc).__name__,
            path=str(notice_path),
            exc_info=True,
        )


def _attempt_child_env(spec: CmdSpec, notice_path: Path) -> dict[str, str]:
    env = dict(spec.env)
    env[SESSION_LIFETIME_NOTICE_ENV_VAR] = str(notice_path)
    return env


def attempt_exit_status(result: CookAttemptResult, *, stream: TextIO = sys.stderr) -> int:
    """Render a cook termination reason and return its shell exit status."""
    if result.termination is TerminationReason.IDLE_STALL:
        stream.write(
            "Interactive cook stopped after being idle past its lifetime limit, "
            "process_tether.cook_ceiling_seconds. Use --resume to continue.\n"
        )
        return LIFETIME_EXIT_STATUS
    if result.termination is TerminationReason.TIMED_OUT:
        stream.write(
            "Interactive cook reached its hard cap, configured by "
            "process_tether.cook_ceiling_seconds and "
            "process_tether.cook_max_extension_seconds.\n"
        )
        return LIFETIME_EXIT_STATUS
    if result.returncode is None:
        raise ValueError("a non-lifetime cook result must have a return code")
    if result.returncode < 0:
        number = -result.returncode
        try:
            signal_name = signal.Signals(number).name
        except ValueError:
            signal_name = f"signal {number}"
        stream.write(
            f"Interactive cook exited from {signal_name} sent outside this cook process.\n"
        )
        return 128 + number
    return result.returncode


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


def _interactive_terminal_fd() -> int | None:
    try:
        terminal_fd = sys.stdin.fileno()
    except (OSError, TypeError, ValueError):
        return None
    if not os.isatty(terminal_fd):
        return None
    return terminal_fd


@contextlib.contextmanager
def _foreground_process_group(pgid: int) -> Iterator[None]:
    """Temporarily transfer an inherited controlling terminal to the child."""
    terminal_fd = _interactive_terminal_fd()
    if terminal_fd is None:
        yield
        return

    previous_pgid = os.tcgetpgrp(terminal_fd)
    primary_error: BaseException | None = None
    try:
        _safe_tcsetpgrp(terminal_fd, pgid)
        try:
            os.killpg(pgid, signal.SIGCONT)
        except ProcessLookupError:
            logger.debug("owned_process_foreground_cont_lookup_raced", extra={"pgid": pgid})
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            _safe_tcsetpgrp(terminal_fd, previous_pgid)
        except BaseException as restore_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "foreground process-group restoration also failed: "
                f"{type(restore_error).__name__}: {restore_error}"
            )


def _safe_tcsetpgrp(terminal_fd: int, pgid: int) -> None:
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
    try:
        os.tcsetpgrp(terminal_fd, pgid)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _wait_for_owned_exit(owner: OwnedProcessGroup, life: InteractiveLifetime) -> None:
    """Observe the child until it exits or the lifetime policy decides."""
    while owner.observe_exit() is None and life.poll() is None:
        time.sleep(_GROUP_POLL_SECONDS)


__all__ = [
    "CookAttemptResult",
    "LIFETIME_EXIT_STATUS",
    "attempt_exit_status",
    "run_cook_attempt",
]
