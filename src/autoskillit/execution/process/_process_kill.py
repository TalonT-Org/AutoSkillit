"""Observation-only cleanup and live owned-process-group lifecycle management."""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import anyio
import anyio.abc
import psutil

from autoskillit.core import ProcessCleanupResult, get_logger

logger = get_logger(__name__)

_FINAL_WAIT_SECONDS = 1.0
_POLL_SECONDS = 0.02
_OWNED_PROCESS_SPAWN_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ProcessObservationSnapshot:
    """Immutable process identities and fail-closed observation evidence."""

    process_identities: tuple[tuple[int, float], ...] = ()
    access_denied_pids: tuple[int, ...] = ()
    observation_complete: bool = True

    def merge(self, other: ProcessObservationSnapshot) -> ProcessObservationSnapshot:
        return ProcessObservationSnapshot(
            process_identities=tuple(
                sorted(set(self.process_identities) | set(other.process_identities))
            ),
            access_denied_pids=tuple(
                sorted(set(self.access_denied_pids) | set(other.access_denied_pids))
            ),
            observation_complete=self.observation_complete and other.observation_complete,
        )


class OwnedProcessCleanupError(RuntimeError):
    """Raised when bounded cleanup cannot produce complete evidence and a reap."""

    def __init__(self, leader_pid: int, cleanup_result: ProcessCleanupResult) -> None:
        super().__init__(f"owned process group {leader_pid} cleanup was incomplete")
        self.leader_pid = leader_pid
        self.cleanup_result = cleanup_result


def _is_disappearance(exc: BaseException) -> bool:
    return isinstance(exc, (psutil.NoSuchProcess, ProcessLookupError)) or (
        isinstance(exc, OSError) and exc.errno == errno.ESRCH
    )


def _is_denial(exc: BaseException) -> bool:
    return isinstance(exc, (psutil.AccessDenied, PermissionError)) or (
        isinstance(exc, OSError) and exc.errno in {errno.EACCES, errno.EPERM}
    )


def _identity(proc: psutil.Process) -> tuple[int, float]:
    return proc.pid, proc.create_time()


def _snapshot_process_tree(pid: int) -> ProcessObservationSnapshot:
    """Capture a root and recursive descendants without signaling or waiting."""
    identities: set[tuple[int, float]] = set()
    denied: set[int] = set()
    complete = True
    try:
        root = psutil.Process(pid)
    except (psutil.Error, OSError) as exc:
        if _is_disappearance(exc):
            return ProcessObservationSnapshot(observation_complete=False)
        if _is_denial(exc):
            return ProcessObservationSnapshot(
                access_denied_pids=(pid,), observation_complete=False
            )
        logger.warning("process_root_observation_failed", pid=pid, exc_info=True)
        return ProcessObservationSnapshot(observation_complete=False)

    try:
        identities.add(_identity(root))
    except (psutil.Error, OSError) as exc:
        if _is_disappearance(exc):
            return ProcessObservationSnapshot(observation_complete=False)
        if _is_denial(exc):
            denied.add(pid)
        else:
            logger.warning("process_root_identity_failed", pid=pid, exc_info=True)
        complete = False

    try:
        children = root.children(recursive=True)
    except (psutil.Error, OSError) as exc:
        children = []
        if _is_denial(exc):
            denied.add(pid)
        elif not _is_disappearance(exc):
            logger.warning("process_descendant_observation_failed", pid=pid, exc_info=True)
        complete = False

    for child in children:
        try:
            identities.add(_identity(child))
        except (psutil.Error, OSError) as exc:
            if _is_disappearance(exc):
                continue
            if _is_denial(exc):
                denied.add(child.pid)
            else:
                logger.warning("process_identity_observation_failed", pid=child.pid, exc_info=True)
            complete = False
    return ProcessObservationSnapshot(
        process_identities=tuple(sorted(identities)),
        access_denied_pids=tuple(sorted(denied)),
        observation_complete=complete,
    )


def kill_process_tree(
    pid: int,
    timeout: float = 2.0,
) -> ProcessCleanupResult:
    """Observe and signal one positively identified PID tree, never a numeric PGID.

    This recovery primitive cannot reconstruct ownership.  A missing root is
    therefore incomplete evidence because its descendants cannot be enumerated.
    Expected disappearance is distinct from permission denial.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    if timeout < 0:
        raise ValueError("timeout must be non-negative")

    denied: set[int] = set()
    complete = True
    try:
        parent = psutil.Process(pid)
    except (psutil.Error, OSError) as exc:
        if _is_disappearance(exc):
            return ProcessCleanupResult(root_pid=pid, observation_complete=False)
        if _is_denial(exc):
            return ProcessCleanupResult(
                root_pid=pid,
                access_denied_pids=(pid,),
                observation_complete=False,
            )
        logger.warning("process_root_lookup_failed", pid=pid, exc_info=True)
        return ProcessCleanupResult(root_pid=pid, observation_complete=False)

    try:
        children = parent.children(recursive=True)
    except (psutil.Error, OSError) as exc:
        children = []
        if _is_denial(exc):
            denied.add(pid)
        elif not _is_disappearance(exc):
            logger.warning("process_descendant_enumeration_failed", pid=pid, exc_info=True)
        complete = False

    all_procs = [*children, parent]
    identities: list[tuple[int, float]] = []
    signal_targets: list[psutil.Process] = []
    for proc in all_procs:
        try:
            identities.append(_identity(proc))
            signal_targets.append(proc)
        except (psutil.Error, OSError) as exc:
            if _is_disappearance(exc):
                continue
            if _is_denial(exc):
                denied.add(proc.pid)
            else:
                logger.warning("process_identity_capture_failed", pid=proc.pid, exc_info=True)
            complete = False

    for proc in signal_targets:
        try:
            proc.send_signal(signal.SIGTERM)
        except (psutil.Error, OSError) as exc:
            if _is_disappearance(exc):
                continue
            if _is_denial(exc):
                denied.add(proc.pid)
            else:
                logger.warning("process_term_failed", pid=proc.pid, exc_info=True)
            complete = False

    try:
        _, alive_after_term = psutil.wait_procs(signal_targets, timeout=timeout)
    except psutil.TimeoutExpired as exc:
        alive_after_term = list(signal_targets)
        logger.debug("process_term_wait_timed_out", pid=getattr(exc, "pid", pid))
    except (psutil.Error, OSError) as exc:
        if _is_disappearance(exc):
            alive_after_term = []
        else:
            alive_after_term = list(signal_targets)
        if _is_denial(exc):
            denied_pid = getattr(exc, "pid", pid)
            denied.add(denied_pid)
            complete = False
        elif not _is_disappearance(exc):
            logger.warning("process_term_wait_failed", pid=pid, exc_info=True)
            complete = False

    for proc in alive_after_term:
        try:
            proc.send_signal(signal.SIGKILL)
        except (psutil.Error, OSError) as exc:
            if _is_disappearance(exc):
                continue
            if _is_denial(exc):
                denied.add(proc.pid)
            else:
                logger.warning("process_kill_failed", pid=proc.pid, exc_info=True)
            complete = False

    try:
        _, alive_after_kill = psutil.wait_procs(alive_after_term, timeout=_FINAL_WAIT_SECONDS)
    except psutil.TimeoutExpired:
        alive_after_kill = list(alive_after_term)
    except (psutil.Error, OSError) as exc:
        if _is_disappearance(exc):
            alive_after_kill = []
        else:
            alive_after_kill = list(alive_after_term)
        if _is_denial(exc):
            denied_pid = getattr(exc, "pid", pid)
            denied.add(denied_pid)
            complete = False
        elif not _is_disappearance(exc):
            logger.warning("process_kill_wait_failed", pid=pid, exc_info=True)
            complete = False
    survivor_pids = tuple(sorted(proc.pid for proc in alive_after_kill))
    observed_pids = {observed_pid for observed_pid, _ in identities}
    terminated_pids = tuple(sorted(observed_pids - set(survivor_pids)))
    return ProcessCleanupResult(
        root_pid=pid,
        process_identities=tuple(sorted(identities)),
        terminated_pids=terminated_pids,
        survivor_pids=survivor_pids,
        access_denied_pids=tuple(sorted(denied)),
        observation_complete=complete,
    )


async def async_kill_process_tree(
    pid: int,
    timeout: float = 2.0,
) -> ProcessCleanupResult:
    """Run observation-only PID-tree cleanup without group authority or event-loop blocking."""
    return await anyio.to_thread.run_sync(kill_process_tree, pid, timeout)


class OwnedProcessGroup:
    """Live capability for a freshly spawned, unreaped direct-child group leader.

    The spawn-bound capability is controller-local and ends permanently when
    an ordinary poll or wait reaps the leader. Stored PIDs and PGIDs cannot
    recreate it. Settlement observes only the bounded group scope while the
    direct leader prevents PGID reuse; descendants that escape the group are
    outside that guarantee.
    """

    def __init__(
        self,
        process: subprocess.Popen[Any],
        pgid: int,
        *,
        _spawn_token: object | None = None,
    ) -> None:
        if _spawn_token is not _OWNED_PROCESS_SPAWN_TOKEN:
            raise TypeError("OwnedProcessGroup instances must come from spawn_owned_process()")
        self.process = process
        self.pgid = pgid
        self.pid = process.pid
        self._group_authority = True
        self._reaped = False
        self._observed_returncode: int | None = None
        self._snapshot = _snapshot_process_tree(self.pid)

    @property
    def snapshot(self) -> ProcessObservationSnapshot:
        return self._snapshot

    @property
    def returncode(self) -> int | None:
        return self._observed_returncode

    @property
    def supports_nonreaping_observation(self) -> bool:
        return hasattr(os, "waitid") and hasattr(os, "WNOWAIT")

    def capture_snapshot(self) -> ProcessObservationSnapshot:
        captured = _snapshot_process_tree(self.pid)
        self._snapshot = self._snapshot.merge(captured)
        return self._snapshot

    def merge_snapshot(self, snapshot: ProcessObservationSnapshot) -> None:
        """Carry earlier pre-poll evidence into final settlement."""
        self._snapshot = self._snapshot.merge(snapshot)

    def observe_exit(self) -> int | None:
        """Observe leader exit without reaping when WNOWAIT is available."""
        if self._observed_returncode is not None:
            return self._observed_returncode
        if self.process.returncode is not None:
            self._group_authority = False
            self._reaped = True
            self._observed_returncode = self.process.returncode
            return self._observed_returncode
        if self.supports_nonreaping_observation:
            try:
                status = os.waitid(  # type: ignore[attr-defined]
                    os.P_PID,
                    self.pid,
                    os.WEXITED | os.WNOHANG | os.WNOWAIT,  # type: ignore[attr-defined]
                )
            except ChildProcessError:
                self._group_authority = False
                self._reaped = True
                self._snapshot = self._snapshot.merge(
                    ProcessObservationSnapshot(observation_complete=False)
                )
                return self.process.returncode
            except PermissionError:
                self._snapshot = self._snapshot.merge(
                    ProcessObservationSnapshot(
                        access_denied_pids=(self.pid,), observation_complete=False
                    )
                )
                return None
            if status is None:
                return None
            if status.si_code == os.CLD_EXITED:
                self._observed_returncode = status.si_status
            else:
                self._observed_returncode = -status.si_status
            return self._observed_returncode

        self.capture_snapshot()
        returncode = self.process.poll()
        if returncode is not None:
            self._group_authority = False
            self._reaped = True
            self._observed_returncode = returncode
        return returncode

    def _validate_group_authority(self) -> bool:
        if (
            not self._group_authority
            or self._reaped
            or self.process.returncode is not None
            or self.pid <= 0
            or self.pgid != self.pid
        ):
            self._snapshot = self._snapshot.merge(
                ProcessObservationSnapshot(observation_complete=False)
            )
            return False
        try:
            valid = os.getpgid(self.pid) == self.pgid
        except OSError as exc:
            valid = False
            if _is_denial(exc):
                self._snapshot = self._snapshot.merge(
                    ProcessObservationSnapshot(
                        access_denied_pids=(self.pid,), observation_complete=False
                    )
                )
                return False
        if not valid:
            self._snapshot = self._snapshot.merge(
                ProcessObservationSnapshot(observation_complete=False)
            )
        return valid

    def _signal_group(self, signum: signal.Signals) -> None:
        if not self._validate_group_authority():
            return
        try:
            os.killpg(self.pgid, signum)
        except ProcessLookupError:
            return
        except OSError as exc:
            if _is_denial(exc):
                self._snapshot = self._snapshot.merge(
                    ProcessObservationSnapshot(
                        access_denied_pids=(self.pgid,), observation_complete=False
                    )
                )
            elif not _is_disappearance(exc):
                logger.warning("owned_group_signal_failed", pgid=self.pgid, exc_info=True)
                self._snapshot = self._snapshot.merge(
                    ProcessObservationSnapshot(observation_complete=False)
                )

    def _identity_is_alive(self, identity: tuple[int, float]) -> bool:
        pid, create_time = identity
        try:
            return psutil.Process(pid).create_time() == create_time
        except (psutil.Error, OSError) as exc:
            if _is_disappearance(exc):
                return False
            if _is_denial(exc):
                self._snapshot = self._snapshot.merge(
                    ProcessObservationSnapshot(
                        access_denied_pids=(pid,), observation_complete=False
                    )
                )
            else:
                logger.warning("owned_group_identity_revalidation_failed", pid=pid, exc_info=True)
                self._snapshot = self._snapshot.merge(
                    ProcessObservationSnapshot(observation_complete=False)
                )
            return True

    def _scan_group(self) -> tuple[tuple[int, float], ...]:
        identities: set[tuple[int, float]] = set()
        denied: set[int] = set()
        complete = True
        try:
            candidates = psutil.process_iter()
            for candidate in candidates:
                if candidate.pid in {self.pid, os.getpid()}:
                    continue
                try:
                    if os.getpgid(candidate.pid) != self.pgid:
                        continue
                    identities.add(_identity(candidate))
                except (psutil.Error, OSError) as exc:
                    if _is_disappearance(exc):
                        continue
                    if _is_denial(exc):
                        denied.add(candidate.pid)
                    else:
                        logger.warning(
                            "owned_group_member_observation_failed",
                            pid=candidate.pid,
                            exc_info=True,
                        )
                    complete = False
        except (psutil.Error, OSError):
            logger.warning("owned_group_enumeration_failed", pgid=self.pgid, exc_info=True)
            complete = False
        observed = ProcessObservationSnapshot(
            process_identities=tuple(sorted(identities)),
            access_denied_pids=tuple(sorted(denied)),
            observation_complete=complete,
        )
        self._snapshot = self._snapshot.merge(observed)
        return observed.process_identities

    def _wait_group_members(self, timeout: float) -> tuple[tuple[int, float], ...]:
        deadline = time.monotonic() + timeout
        while True:
            members = tuple(
                identity for identity in self._scan_group() if self._identity_is_alive(identity)
            )
            self.observe_exit()
            if not members or time.monotonic() >= deadline:
                return members
            time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.monotonic())))

    def _bounded_direct_reap(self, timeout: float) -> int | None:
        if self._reaped:
            return self.process.returncode
        try:
            returncode = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        self._reaped = True
        self._group_authority = False
        self._observed_returncode = returncode
        return returncode

    def cleanup(self, timeout: float = 2.0) -> tuple[int | None, ProcessCleanupResult]:
        """Settle the owned group and reap the leader with bounded waits."""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        self.capture_snapshot()
        self._scan_group()
        self._signal_group(signal.SIGTERM)
        members = self._wait_group_members(timeout)
        if members or self.observe_exit() is None:
            self._signal_group(signal.SIGKILL)
            members = self._wait_group_members(_FINAL_WAIT_SECONDS)

        returncode = self.observe_exit()
        if returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            except PermissionError:
                self._snapshot = self._snapshot.merge(
                    ProcessObservationSnapshot(
                        access_denied_pids=(self.pid,), observation_complete=False
                    )
                )
            returncode = self._bounded_direct_reap(timeout)
        else:
            returncode = self._bounded_direct_reap(timeout)
        if returncode is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
            except PermissionError:
                self._snapshot = self._snapshot.merge(
                    ProcessObservationSnapshot(
                        access_denied_pids=(self.pid,), observation_complete=False
                    )
                )
            returncode = self._bounded_direct_reap(_FINAL_WAIT_SECONDS)

        identities = set(self._snapshot.process_identities)
        survivors = tuple(
            sorted(pid for pid, created in identities if self._identity_is_alive((pid, created)))
        )
        observed_pids = {pid for pid, _ in identities}
        result = ProcessCleanupResult(
            root_pid=self.pid,
            process_identities=tuple(sorted(identities)),
            terminated_pids=tuple(sorted(observed_pids - set(survivors))),
            survivor_pids=survivors,
            access_denied_pids=self._snapshot.access_denied_pids,
            observation_complete=(
                self._snapshot.observation_complete and not members and returncode is not None
            ),
        )
        return returncode, result

    def settle(self, timeout: float = 2.0) -> tuple[int, ProcessCleanupResult]:
        returncode, result = self.cleanup(timeout)
        if returncode is None or not result.complete:
            raise OwnedProcessCleanupError(self.pid, result)
        return returncode, result

    def settle_preserving(
        self, error: BaseException, timeout: float = 2.0
    ) -> ProcessCleanupResult:
        _, result = self.cleanup(timeout)
        if not result.complete:
            logger.error("owned_group_cleanup_incomplete", evidence=result.to_dict())
            error.add_note(f"owned process cleanup evidence: {result.to_dict()}")
        return result


def spawn_owned_process(
    args: Sequence[str] | str,
    *,
    start_new_session: bool = False,
    process_group: int | None = None,
    env: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> OwnedProcessGroup:
    """Atomically spawn and validate a fresh owned POSIX process group."""
    creates_group = start_new_session is True or process_group == 0
    if not creates_group or (start_new_session and process_group == 0):
        raise ValueError("owned process spawn requires exactly one fresh-group mode")
    if os.name != "posix" or not hasattr(os, "killpg"):
        raise RuntimeError("owned process groups require POSIX group signaling")
    popen_kwargs = dict(kwargs)
    popen_kwargs["start_new_session"] = start_new_session
    if process_group is not None:
        popen_kwargs["process_group"] = process_group
    if env is not None:
        popen_kwargs["env"] = dict(env)
    process = subprocess.Popen(args, **popen_kwargs)
    try:
        pgid = os.getpgid(process.pid)
    except BaseException:
        process.kill()
        process.wait(timeout=_FINAL_WAIT_SECONDS)
        raise
    if process.pid <= 0 or pgid != process.pid or process.returncode is not None:
        process.kill()
        process.wait(timeout=_FINAL_WAIT_SECONDS)
        raise RuntimeError("spawned child did not establish owned group leadership")
    return OwnedProcessGroup(process, pgid, _spawn_token=_OWNED_PROCESS_SPAWN_TOKEN)


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
