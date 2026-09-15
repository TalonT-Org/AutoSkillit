"""Spawn-provenanced owned process-group lifecycle management."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from autoskillit.core import (
    ProcessCleanupResult,
    get_logger,
    read_boot_id,
    read_pid_namespace_inode,
    read_starttime_ticks,
)
from autoskillit.execution.process._process_kill import (
    _FINAL_WAIT_SECONDS,
    _identity,
    _is_denial,
    _is_disappearance,
    kill_process_tree,
)
from autoskillit.execution.process._process_tether import (
    TetherRecord,
    TetherSpec,
    default_tether_dir,
    remove_tether,
    write_tether,
)

logger = get_logger(__name__)

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


def _incomplete_observation(
    pid: int, exc: BaseException, event: str
) -> ProcessObservationSnapshot:
    if _is_denial(exc):
        return ProcessObservationSnapshot(access_denied_pids=(pid,), observation_complete=False)
    if not _is_disappearance(exc):
        logger.warning(event, pid=pid, exc_info=True)
    return ProcessObservationSnapshot(observation_complete=False)


def _snapshot_descendants(root: psutil.Process) -> ProcessObservationSnapshot:
    """Capture stable descendant identities without treating their exit as a failure."""
    try:
        children = root.children(recursive=True)
    except (psutil.Error, OSError) as exc:
        return _incomplete_observation(root.pid, exc, "process_descendant_observation_failed")
    identities: set[tuple[int, float]] = set()
    denied: set[int] = set()
    complete = True
    for child in children:
        try:
            identities.add(_identity(child))
        except (psutil.Error, OSError) as exc:
            if _is_disappearance(exc):
                continue
            complete = False
            if _is_denial(exc):
                denied.add(child.pid)
            else:
                logger.warning("process_identity_observation_failed", pid=child.pid, exc_info=True)
    return ProcessObservationSnapshot(
        process_identities=tuple(sorted(identities)),
        access_denied_pids=tuple(sorted(denied)),
        observation_complete=complete,
    )


def _snapshot_process_tree(pid: int) -> ProcessObservationSnapshot:
    """Capture a root and recursive descendants without signaling or waiting."""
    try:
        root = psutil.Process(pid)
    except (psutil.Error, OSError) as exc:
        return _incomplete_observation(pid, exc, "process_root_observation_failed")
    try:
        root_evidence = ProcessObservationSnapshot(process_identities=(_identity(root),))
    except (psutil.Error, OSError) as exc:
        if _is_disappearance(exc):
            return ProcessObservationSnapshot(observation_complete=False)
        root_evidence = _incomplete_observation(pid, exc, "process_root_identity_failed")
    return root_evidence.merge(_snapshot_descendants(root))


class OwnedProcessGroup:
    """Live capability for a freshly spawned, unreaped direct-child group leader.

    The spawn-bound capability is controller-local and ends permanently when
    an ordinary poll or wait reaps the leader. Stored PIDs and PGIDs cannot
    recreate it. Settlement always records descendants observed through the
    leader; abort-originated settlement may also reap descendants that escape
    the group by revalidating those recorded identities.
    """

    def __init__(
        self,
        process: subprocess.Popen[Any],
        pgid: int,
        *,
        _spawn_token: object | None = None,
        tether_path: Path | None = None,
    ) -> None:
        if _spawn_token is not _OWNED_PROCESS_SPAWN_TOKEN:
            raise TypeError("OwnedProcessGroup instances must come from spawn_owned_process()")
        self.process = process
        self.pgid = pgid
        self.pid = process.pid
        self._tether_path = tether_path
        self._group_authority = True
        self._reaped = False
        self._observed_returncode: int | None = None
        self._snapshot = _snapshot_process_tree(self.pid)

    @property
    def snapshot(self) -> ProcessObservationSnapshot:
        return self._snapshot

    @property
    def tether_path(self) -> Path | None:
        return self._tether_path

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

    def _record_incomplete(self, denied_pid: int | None = None) -> None:
        self._snapshot = self._snapshot.merge(
            ProcessObservationSnapshot(
                access_denied_pids=() if denied_pid is None else (denied_pid,),
                observation_complete=False,
            )
        )

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
                self._record_incomplete()
                return self.process.returncode
            except PermissionError:
                self._record_incomplete(self.pid)
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
            self._record_incomplete()
            return False
        try:
            valid = os.getpgid(self.pid) == self.pgid
        except OSError as exc:
            valid = False
            if _is_denial(exc):
                self._record_incomplete(self.pid)
                return False
            if not _is_disappearance(exc):
                logger.warning(
                    "owned_group_authority_validation_failed",
                    pid=self.pid,
                    pgid=self.pgid,
                    exc_info=True,
                )
        if not valid:
            self._record_incomplete()
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
                self._record_incomplete(self.pgid)
            elif not _is_disappearance(exc):
                logger.warning("owned_group_signal_failed", pgid=self.pgid, exc_info=True)
                self._record_incomplete()

    def _identity_is_alive(self, identity: tuple[int, float]) -> bool:
        """Return whether the identified PID is still a live, non-zombie process."""
        pid, create_time = identity
        try:
            proc = psutil.Process(pid)
            return proc.create_time() == create_time and proc.status() != psutil.STATUS_ZOMBIE
        except (psutil.Error, OSError) as exc:
            if _is_disappearance(exc):
                return False
            if _is_denial(exc):
                self._record_incomplete(pid)
            else:
                logger.warning("owned_group_identity_revalidation_failed", pid=pid, exc_info=True)
                self._record_incomplete()
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

    def _signal_direct_leader(self, signum: signal.Signals) -> None:
        signal_leader = self.process.terminate if signum == signal.SIGTERM else self.process.kill
        try:
            signal_leader()
        except ProcessLookupError:
            pass  # expected race: leader can exit between exit-poll and this signal
        except PermissionError:
            self._record_incomplete(self.pid)

    def cleanup(
        self, timeout: float = 2.0, *, escalate: bool = False
    ) -> tuple[int | None, ProcessCleanupResult]:
        """Settle the owned group and reap the leader with bounded waits."""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        self.capture_snapshot()
        self._scan_group()
        identities = set(self._snapshot.process_identities)
        self._signal_group(signal.SIGTERM)
        members = self._wait_group_members(timeout)
        escalated = False
        if members or self.observe_exit() is None:
            self._signal_group(signal.SIGKILL)
            members = self._wait_group_members(_FINAL_WAIT_SECONDS)
            escalated = True

        returncode = self.observe_exit()
        if returncode is None:
            self._signal_direct_leader(signal.SIGTERM)
        returncode = self._bounded_direct_reap(_FINAL_WAIT_SECONDS if escalated else timeout)
        if returncode is None:
            self._signal_direct_leader(signal.SIGKILL)
            returncode = self._bounded_direct_reap(_FINAL_WAIT_SECONDS)

        surviving_identities = tuple(
            sorted(identity for identity in identities if self._identity_is_alive(identity))
        )
        if escalate and surviving_identities:
            escalation_deadline = time.monotonic() + timeout
            for pid, create_time in surviving_identities:
                cleanup = kill_process_tree(
                    pid,
                    timeout=timeout,
                    expected_create_time=create_time,
                    deadline=escalation_deadline,
                )
                if cleanup.access_denied_pids:
                    self._snapshot = self._snapshot.merge(
                        ProcessObservationSnapshot(
                            access_denied_pids=cleanup.access_denied_pids,
                            observation_complete=False,
                        )
                    )
            # A signal can be accepted at the deadline before the scheduler has
            # published process exit. Yield once without extending the signal budget.
            time.sleep(_POLL_SECONDS)
            surviving_identities = tuple(
                identity for identity in identities if self._identity_is_alive(identity)
            )
        survivors = tuple(sorted(pid for pid, _ in surviving_identities))
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
        if result.complete and self._tether_path is not None:
            # Best-effort — the sweep is the authoritative GC if this races or fails.
            remove_tether(self._tether_path)
        return returncode, result

    def settle(
        self, timeout: float = 2.0, *, escalate: bool = False
    ) -> tuple[int, ProcessCleanupResult]:
        """Settle the owned group and raise on incomplete cleanup."""
        if escalate:
            returncode, result = self.cleanup(timeout, escalate=True)
        else:
            returncode, result = self.cleanup(timeout)
        if returncode is None or not result.complete:
            raise OwnedProcessCleanupError(self.pid, result)
        return returncode, result

    def settle_evidence(
        self, timeout: float = 2.0, *, escalate: bool = False
    ) -> tuple[int | None, ProcessCleanupResult]:
        """Settle the owned group and return cleanup evidence without raising."""
        if escalate:
            returncode, result = self.cleanup(timeout, escalate=True)
        else:
            returncode, result = self.cleanup(timeout)
        if not result.complete or returncode is None:
            logger.error(
                "owned_group_cleanup_incomplete",
                evidence=result.to_dict(),
                returncode_confirmed=returncode is not None,
            )
        return returncode, result

    def settle_preserving(
        self, error: BaseException, timeout: float = 2.0, *, escalate: bool = False
    ) -> ProcessCleanupResult:
        """Settle the owned group while preserving a caller-supplied exception."""
        try:
            if escalate:
                _, result = self.cleanup(timeout, escalate=True)
            else:
                _, result = self.cleanup(timeout)
        except BaseException as cleanup_error:
            logger.error(
                "owned_group_cleanup_failed",
                pid=self.pid,
                error_type=type(cleanup_error).__name__,
                exc_info=True,
            )
            survivor_pids = {pid for pid, _ in self._snapshot.process_identities}
            if self.process.returncode is None:
                survivor_pids.add(self.pid)
            result = ProcessCleanupResult(
                root_pid=self.pid,
                process_identities=self._snapshot.process_identities,
                survivor_pids=tuple(sorted(survivor_pids)),
                access_denied_pids=self._snapshot.access_denied_pids,
                observation_complete=False,
            )
            error.add_note(
                "owned process cleanup failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}; "
                f"evidence: {result.to_dict()}"
            )
            return result
        if not result.complete:
            logger.error("owned_group_cleanup_incomplete", evidence=result.to_dict())
            error.add_note(f"owned process cleanup evidence: {result.to_dict()}")
        return result


def _cleanup_failed_owned_spawn(process: subprocess.Popen[Any]) -> None:
    try:
        process.kill()
    except BaseException:
        logger.warning("owned_process_spawn_kill_failed", pid=process.pid, exc_info=True)
    try:
        process.wait(timeout=_FINAL_WAIT_SECONDS)
    except BaseException:
        logger.warning("owned_process_spawn_reap_failed", pid=process.pid, exc_info=True)


def spawn_owned_process(
    args: Sequence[str] | str,
    *,
    start_new_session: bool = False,
    process_group: int | None = None,
    env: Mapping[str, str] | None = None,
    tether: TetherSpec,
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
        _cleanup_failed_owned_spawn(process)
        raise
    if process.pid <= 0 or pgid != process.pid or process.returncode is not None:
        _cleanup_failed_owned_spawn(process)
        raise RuntimeError("spawned child did not establish owned group leadership")

    child_starttime_ticks = read_starttime_ticks(process.pid)
    boot_id = read_boot_id()
    if sys.platform == "linux" and (child_starttime_ticks is None or boot_id is None):
        _cleanup_failed_owned_spawn(process)
        raise RuntimeError("owned process spawned but its identity could not be read")

    spawner_pid = os.getpid()
    tether_dir = tether.tether_dir if tether.tether_dir is not None else default_tether_dir()
    record = TetherRecord(
        child_pid=process.pid,
        child_pgid=pgid,
        child_starttime_ticks=child_starttime_ticks or 0,
        boot_id=boot_id or "",
        spawner_pid=spawner_pid,
        spawner_starttime_ticks=read_starttime_ticks(spawner_pid) or 0,
        spawned_at_ns=time.time_ns(),
        not_after=time.time() + tether.ceiling_seconds,
        origin=tether.origin,
        pidns_inode=read_pid_namespace_inode(process.pid),
    )
    try:
        tether_path = write_tether(record, tether_dir)
    except OSError:
        _cleanup_failed_owned_spawn(process)
        raise

    return OwnedProcessGroup(
        process, pgid, _spawn_token=_OWNED_PROCESS_SPAWN_TOKEN, tether_path=tether_path
    )
