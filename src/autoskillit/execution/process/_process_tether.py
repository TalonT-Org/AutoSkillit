"""Process-tether registry: fail-closed spawner-death immunity for detached children.

A tether is a small JSON record written as a mandatory side effect of every
funnel spawn (see ``spawn_owned_process`` in ``_lifecycle/owned_group.py``). It carries
the spawner's identity, the child's identity, and a ``not_after`` lease.
Supervising owners renew the lease; owners that do not supervise, such as
headless children of long-lived servers, keep their spawn-time ceiling as an
absolute bound. The sweep treats a lapsed lease as abandonment in either case.
It acts only when the child's (or PTY-wrapper workload's) identity is positively
re-verified, so kills are never issued on ambiguous evidence.

A tether may also carry an owner-scope token (see ``_lifecycle/owner_scope.py``).
Sealing a scope writes a ``<token>.sealed`` marker beside the tethers, after
which the funnel refuses further spawns into that scope.

Linux-only: identity primitives (`read_boot_id`/`read_starttime_ticks`) return
``None`` on every other platform, so writing and sweeping are no-ops there —
the same platform gating `bind_session_owner` already uses.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Final

import anyio

from autoskillit.core import (
    ProcessCleanupResult,
    atomic_write,
    default_log_dir,
    get_logger,
    is_pid_alive,
    is_session_alive,
    read_pid_namespace_inode,
    read_versioned_json,
    write_versioned_json,
)

logger = get_logger(__name__)

DEFAULT_TETHER_CEILING_SECONDS: Final = 86400.0
TETHER_SWEEP_INTERVAL_SECONDS: Final = 1800.0
TETHER_LEASE_SECONDS: Final = 4 * TETHER_SWEEP_INTERVAL_SECONDS
TETHER_LEASE_RENEW_SECONDS: Final = TETHER_LEASE_SECONDS / 4
# Seal-marker sweep TTL. Two invariants must hold so a sealed marker cannot
# be swept while its scope is still settling or has any tether within its
# not_after window:
#   1. outlive every possible tether ceiling -- enforced by ``TetherSpec``'s
#      upper-bound check below, which caps ``ceiling_seconds`` at
#      ``DEFAULT_TETHER_CEILING_SECONDS``.
#   2. outlive the owner-scope settlement timeout
#      (``OWNER_SCOPE_SETTLE_TIMEOUT_SECONDS = 30.0`` in
#      ``_lifecycle/owner_scope.py``) so a seal written at settle start
#      cannot be swept mid-settle.
# Today ``DEFAULT_TETHER_CEILING_SECONDS`` (86400s) exceeds the settle
# timeout by ~3 orders of magnitude, so pinning ``SEAL_MARKER_TTL_SECONDS``
# to it satisfies both invariants. If either constant ever changes, recompute
# this expression -- do not silently let them drift apart.
SEAL_MARKER_TTL_SECONDS: Final = DEFAULT_TETHER_CEILING_SECONDS

_TETHER_DIR_NAME: Final = "process-tethers"
_SEALED_SCOPE_DIR_NAME: Final = "sealed-scopes"


@dataclass(frozen=True, slots=True)
class TetherRecord:
    """Durable, host-identity-bound proof that a detached child has a guardian.

    ``pidns_inode`` is a kill-preventing discriminator only: present-and-
    mismatched downgrades a target to ``identity_mismatch`` (no kill); absent
    on either side falls back to the boot_id/starttime_ticks triple and never
    enables a kill the triple would have refused.

    ``workload_pid``/``workload_starttime_ticks`` are unset at spawn time and
    filled in later, only for PTY-wrapped spawns, via ``update_tether_workload``
    once the real workload's identity has been resolved.

    ``owner_scope`` names the owner scope whose settlement must end this child
    before the owner returns; ``None`` for spawns made outside any scope.
    """

    child_pid: int
    child_pgid: int
    child_starttime_ticks: int
    boot_id: str
    spawner_pid: int
    spawner_starttime_ticks: int
    spawned_at_ns: int
    not_after: float
    origin: str
    schema_version: int = 1
    pidns_inode: int | None = None
    workload_pid: int | None = None
    workload_starttime_ticks: int | None = None
    owner_scope: str | None = None

    def __post_init__(self) -> None:
        if self.child_pid <= 0 or self.child_pgid <= 0 or self.spawner_pid <= 0:
            raise ValueError("child_pid, child_pgid, and spawner_pid must be positive")
        if self.child_starttime_ticks < 0 or self.spawner_starttime_ticks < 0:
            raise ValueError("starttime_ticks must be non-negative")
        if not math.isfinite(self.not_after):
            raise ValueError(f"not_after must be finite, got {self.not_after}")
        if self.workload_pid is not None and self.workload_pid <= 0:
            raise ValueError("workload_pid must be positive when set")


@dataclass(frozen=True, slots=True)
class TetherSpec:
    """Caller-supplied intent for a single funnel spawn's tether."""

    origin: str
    ceiling_seconds: float
    tether_dir: Path | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.ceiling_seconds) or self.ceiling_seconds <= 0:
            raise ValueError(
                f"ceiling_seconds must be a positive finite number, got {self.ceiling_seconds}"
            )
        if self.ceiling_seconds > DEFAULT_TETHER_CEILING_SECONDS:
            # A ceiling above the seal-marker's TTL would let the sweep remove
            # the seal while tethers for the scope are still within their
            # not_after window, defeating the "no spawn can still target
            # them" invariant. Reject the value up front so the funnel's
            # contract is enforced rather than coincidentally satisfied.
            raise ValueError(
                f"ceiling_seconds ({self.ceiling_seconds}) exceeds the maximum permitted "
                f"({DEFAULT_TETHER_CEILING_SECONDS}). Larger values would defeat the seal-marker "
                "sweep's guarantee that a sealed marker outlives every tether for that scope; "
                f"the sweep's TTL is pinned to SEAL_MARKER_TTL_SECONDS = "
                f"{DEFAULT_TETHER_CEILING_SECONDS}."
            )


@dataclass(frozen=True, slots=True)
class TetherSweepOutcome:
    """The disposition the sweep reached for one tether record."""

    tether_path: str
    child_pid: int
    outcome: str


@dataclass(frozen=True, slots=True)
class TetherSweepReport:
    """Aggregate result of one ``sweep_orphaned_tethers`` pass."""

    outcomes: tuple[TetherSweepOutcome, ...] = ()

    @property
    def reaped_count(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome in ("reaped_orphan", "reaped_ceiling"))


class OwnerScopeSealedError(RuntimeError):
    """Raised when a funnel spawn targets an owner scope that has already been sealed."""

    def __init__(self, token: str) -> None:
        super().__init__(f"owner scope {token!r} is sealed; no further spawns are admitted")
        self.token = token


def default_tether_dir() -> Path:
    """Per-user, host-wide tether directory — every writer and sweeper resolves here."""
    return default_log_dir() / _TETHER_DIR_NAME


def sealed_scope_dir(tether_dir: Path) -> Path:
    """Directory holding one ``<token>.sealed`` marker per sealed owner scope."""
    return tether_dir / _SEALED_SCOPE_DIR_NAME


def seal_owner_scope(tether_dir: Path, token: str) -> None:
    """Atomically mark *token* sealed; idempotent, since owner and reaper may both seal."""
    if sys.platform != "linux":
        return
    seal_dir = sealed_scope_dir(tether_dir)
    seal_dir.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write(seal_dir / f"{token}.sealed", "", exclusive=True)
    except FileExistsError:
        pass


def is_owner_scope_sealed(tether_dir: Path, token: str) -> bool:
    """Whether *token* is sealed — any existing entry counts, matching the O_EXCL claim."""
    if sys.platform != "linux":
        return False
    return os.path.lexists(sealed_scope_dir(tether_dir) / f"{token}.sealed")


@dataclass(frozen=True, slots=True)
class OrphanedTetherRecord:
    """A tether record flagged for reap: spawner dead or ceiling passed."""

    tether_path: str
    record: TetherRecord
    reason: str


def format_orphaned_tether_fields(record: TetherRecord, reason: str) -> str:
    """Shared ``pid=... origin=... reason=...`` rendering for CLI/doctor orphan surfaces."""
    return f"pid={record.child_pid} origin={record.origin} reason={reason}"


def _tether_record_to_dict(record: TetherRecord) -> dict[str, Any]:
    """Payload only — schema_version is added by write_versioned_json at write time."""
    return {
        "child_pid": record.child_pid,
        "child_pgid": record.child_pgid,
        "child_starttime_ticks": record.child_starttime_ticks,
        "boot_id": record.boot_id,
        "pidns_inode": record.pidns_inode,
        "spawner_pid": record.spawner_pid,
        "spawner_starttime_ticks": record.spawner_starttime_ticks,
        "spawned_at_ns": record.spawned_at_ns,
        "not_after": record.not_after,
        "origin": record.origin,
        "workload_pid": record.workload_pid,
        "workload_starttime_ticks": record.workload_starttime_ticks,
        "owner_scope": record.owner_scope,
    }


def _tether_record_from_dict(data: dict[str, Any] | None) -> TetherRecord:
    """Raise ValueError/KeyError/TypeError on any malformed/missing field."""
    if data is None:
        raise TypeError("tether record must be an object")
    return TetherRecord(
        schema_version=int(data["schema_version"]),
        child_pid=int(data["child_pid"]),
        child_pgid=int(data["child_pgid"]),
        child_starttime_ticks=int(data["child_starttime_ticks"]),
        boot_id=str(data["boot_id"]),
        spawner_pid=int(data["spawner_pid"]),
        spawner_starttime_ticks=int(data["spawner_starttime_ticks"]),
        spawned_at_ns=int(data["spawned_at_ns"]),
        not_after=float(data["not_after"]),
        origin=str(data["origin"]),
        pidns_inode=(int(data["pidns_inode"]) if data.get("pidns_inode") is not None else None),
        workload_pid=(int(data["workload_pid"]) if data.get("workload_pid") is not None else None),
        workload_starttime_ticks=(
            int(data["workload_starttime_ticks"])
            if data.get("workload_starttime_ticks") is not None
            else None
        ),
        owner_scope=(str(data["owner_scope"]) if data.get("owner_scope") is not None else None),
    )


def _tether_path(tether_dir: Path, record: TetherRecord) -> Path:
    return tether_dir / f"{record.child_pid}-{record.spawned_at_ns}.json"


def write_tether(record: TetherRecord, tether_dir: Path) -> Path:
    """Durably write a tether record. Raises on failure — callers must fail closed."""
    if sys.platform != "linux":
        return _tether_path(tether_dir, record)
    tether_dir.mkdir(parents=True, exist_ok=True)
    path = _tether_path(tether_dir, record)
    write_versioned_json(path, _tether_record_to_dict(record), record.schema_version)
    return path


def remove_tether(path: Path) -> None:
    """Best-effort unlink — the sweep is the authoritative GC, this is an optimization."""
    try:
        path.unlink()
    except OSError:
        pass


def update_tether_workload(path: Path, workload_pid: int, workload_starttime_ticks: int) -> None:
    """Best-effort: attach resolved PTY-workload identity to an existing tether.

    Must never raise — this runs after the spawn has already succeeded, and a
    concurrently-settled (and thus already-removed) tether is not an error.
    """
    data = read_versioned_json(path, 1)
    if data is None:
        logger.warning("tether_workload_update_skipped_unreadable", path=str(path))
        return
    data["workload_pid"] = workload_pid
    data["workload_starttime_ticks"] = workload_starttime_ticks
    try:
        write_versioned_json(path, data, 1)
    except OSError:
        logger.warning("tether_workload_update_write_failed", path=str(path))


def renew_tether(path: Path, not_after: float) -> bool:
    """Best-effort lease renewal for a supervising tether owner."""
    if sys.platform != "linux":
        return True
    if not math.isfinite(not_after):
        logger.warning("tether_lease_renew_skipped_invalid_deadline", path=str(path))
        return False
    try:
        data = read_versioned_json(path, 1)
    except Exception:
        logger.warning("tether_lease_renew_skipped_unreadable", path=str(path), exc_info=True)
        return False
    if not isinstance(data, dict):
        logger.warning("tether_lease_renew_skipped_unreadable", path=str(path))
        return False
    data["not_after"] = not_after
    try:
        write_versioned_json(path, data, 1)
    except Exception:
        logger.warning("tether_lease_renew_write_failed", path=str(path), exc_info=True)
        return False
    return True


def _identity_discriminator_mismatch(pid: int, expected_pidns_inode: int | None) -> bool:
    """True only when both sides carry a pidns_inode and they disagree."""
    if expected_pidns_inode is None:
        return False
    actual = read_pid_namespace_inode(pid)
    if actual is None:
        return False
    return actual != expected_pidns_inode


def _target_status(
    pid: int, boot_id: str, starttime_ticks: int | None, pidns_inode: int | None
) -> str:
    """One of "dead", "mismatch", "live" for a single tether target."""
    if not is_pid_alive(pid):
        return "dead"
    if starttime_ticks is None or not is_session_alive(pid, boot_id, starttime_ticks):
        return "mismatch"
    if _identity_discriminator_mismatch(pid, pidns_inode):
        return "mismatch"
    return "live"


def _tether_targets(record: TetherRecord) -> list[tuple[str, int, int | None]]:
    """The ``(name, pid, starttime_ticks)`` kill targets one tether record guards."""
    targets: list[tuple[str, int, int | None]] = [
        ("child", record.child_pid, record.child_starttime_ticks)
    ]
    if record.workload_pid is not None and record.workload_pid != record.child_pid:
        targets.append(("workload", record.workload_pid, record.workload_starttime_ticks))
    return targets


def _tether_target_statuses(record: TetherRecord) -> dict[str, str]:
    return {
        name: _target_status(pid, record.boot_id, ticks, record.pidns_inode)
        for name, pid, ticks in _tether_targets(record)
    }


def _settle_tether_targets(
    record: TetherRecord, statuses: Mapping[str, str]
) -> list[ProcessCleanupResult]:
    """Identity-fenced tree kill of every target *statuses* reports live.

    Returns one cleanup result per kill issued; the targets are confirmed dead
    when every result is complete.
    """
    # Keep the psutil-backed recovery primitive local so importing tether data
    # definitions and registry helpers does not initialize process-control code.
    from autoskillit.execution.process._process_kill import kill_process_tree

    results: list[ProcessCleanupResult] = []
    for name, pid, ticks in _tether_targets(record):
        if statuses[name] != "live":
            continue
        if not is_pid_alive(pid):
            # Reaped as a descendant of an earlier target's kill in this
            # same pass (e.g. the child-wrapper's recursive tree walk).
            continue
        results.append(
            kill_process_tree(pid, expected_boot_id=record.boot_id, expected_starttime_ticks=ticks)
        )
    return results


def find_orphaned_tethers(
    tether_dir: Path, *, min_age_seconds: float = 60.0
) -> list[OrphanedTetherRecord]:
    """Read-only: list tether records whose spawner is dead or ceiling has passed.

    Never kills or removes anything — for doctor/CLI dry-run listing. A
    malformed record is silently skipped here (cleaning those up is
    ``sweep_orphaned_tethers``'s job, not a listing concern).
    """
    if sys.platform != "linux" or not tether_dir.is_dir():
        return []
    now = time.time()
    orphaned: list[OrphanedTetherRecord] = []
    for path in sorted(tether_dir.glob("*.json")):
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age < min_age_seconds:
            continue
        data = read_versioned_json(path, 1)
        try:
            record = _tether_record_from_dict(data)
        except (ValueError, KeyError, TypeError):
            continue
        spawner_alive = is_session_alive(
            record.spawner_pid, record.boot_id, record.spawner_starttime_ticks
        )
        if not spawner_alive:
            orphaned.append(OrphanedTetherRecord(str(path), record, "spawner_dead"))
        elif now >= record.not_after:
            orphaned.append(OrphanedTetherRecord(str(path), record, "ceiling_expired"))
    return orphaned


def sweep_orphaned_tethers(
    tether_dir: Path, *, min_age_seconds: float = 60.0
) -> TetherSweepReport:
    """Reap tethered children whose guardian is dead or whose ceiling has passed.

    Every record younger than ``min_age_seconds`` is skipped untouched,
    whatever its content — a spawn-settling grace period, mirroring fleet's
    ``min_reap_age_seconds``. No kill is ever issued on a target whose
    identity cannot be positively re-verified; any kill failure keeps the
    tether and logs (fail-closed per record).
    """
    if sys.platform != "linux":
        return TetherSweepReport()
    if not tether_dir.is_dir():
        return TetherSweepReport()

    now = time.time()
    outcomes: list[TetherSweepOutcome] = []
    for path in sorted(tether_dir.glob("*.json")):
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue  # vanished concurrently — another sweep won the race
        if age < min_age_seconds:
            continue

        data = read_versioned_json(path, 1)
        try:
            record = _tether_record_from_dict(data)
        except (ValueError, KeyError, TypeError):
            remove_tether(path)
            outcomes.append(TetherSweepOutcome(str(path), -1, "malformed"))
            continue

        statuses = _tether_target_statuses(record)

        if all(status != "live" for status in statuses.values()):
            remove_tether(path)
            outcome = "identity_mismatch" if "mismatch" in statuses.values() else "dead_child"
            outcomes.append(TetherSweepOutcome(str(path), record.child_pid, outcome))
            continue

        spawner_alive = is_session_alive(
            record.spawner_pid, record.boot_id, record.spawner_starttime_ticks
        )
        ceiling_expired = now >= record.not_after
        if spawner_alive and not ceiling_expired:
            outcomes.append(TetherSweepOutcome(str(path), record.child_pid, "kept"))
            continue

        reason = "reaped_orphan" if not spawner_alive else "reaped_ceiling"
        logger.warning(
            "tether_sweep_reap_decision",
            reason=reason,
            origin=record.origin,
            child_pid=record.child_pid,
            spawner_pid=record.spawner_pid,
            spawner_alive=spawner_alive,
            overdue_seconds=max(0.0, now - record.not_after),
            path=str(path),
        )
        results = _settle_tether_targets(record, statuses)
        if all(result.complete for result in results):
            remove_tether(path)
            logger.info(
                "tether_sweep_reaped",
                reason=reason,
                origin=record.origin,
                child_pid=record.child_pid,
                path=str(path),
            )
            outcomes.append(TetherSweepOutcome(str(path), record.child_pid, reason))
        else:
            logger.warning(
                "tether_sweep_kill_incomplete", path=str(path), child_pid=record.child_pid
            )
            outcomes.append(TetherSweepOutcome(str(path), record.child_pid, "kill_failed"))

    _expire_seal_markers(tether_dir, now)
    return TetherSweepReport(outcomes=tuple(outcomes))


def _expire_seal_markers(tether_dir: Path, now: float) -> None:
    """Drop seal markers older than ``SEAL_MARKER_TTL_SECONDS``; no spawn can still target them.

    The TTL bounds both invariants stated at ``SEAL_MARKER_TTL_SECONDS`` --
    every possible tether ceiling (enforced by ``TetherSpec.__post_init__``)
    and the owner-scope settle timeout -- so a sealed marker cannot be
    swept while settlement is in progress or while any tether for that
    scope is still within its not_after window.
    """
    for marker in sorted(sealed_scope_dir(tether_dir).glob("*.sealed")):
        try:
            marker_age = now - marker.stat().st_mtime
        except OSError:
            continue
        if marker_age >= SEAL_MARKER_TTL_SECONDS:
            remove_tether(marker)


async def sweep_orphaned_tethers_async(
    tether_dir: Path, *, min_age_seconds: float = 60.0
) -> TetherSweepReport:
    """Thread-offload wrapper mirroring ``reap_stale_dispatches_async``."""
    return await anyio.to_thread.run_sync(
        partial(sweep_orphaned_tethers, tether_dir, min_age_seconds=min_age_seconds)
    )


def probe_systemd_scope_available() -> bool:
    """Return whether ``systemd-run --user --scope`` is viable on this host.

    Requires ``systemd-run`` on PATH and ``systemctl --user is-system-running``
    to report ``running`` or ``degraded`` — ``degraded`` still means the user
    manager itself is up, only that some unit elsewhere failed. Any probe
    failure (missing binary, non-zero/timeout/unreadable systemctl) returns
    False; callers fall back to an unwrapped spawn plus a warning, never a
    raise — this is defense-in-depth, not the ceiling of record.
    """
    if sys.platform != "linux":
        return False
    if shutil.which("systemd-run") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() in {"running", "degraded"}


def wrap_systemd_scope(cmd: Sequence[str], *, enabled: bool, ceiling_seconds: float) -> list[str]:
    """Prefix *cmd* with a ``systemd-run --user --scope`` wrapper when enabled
    and the host supports it, for a kernel-enforced ceiling that survives
    spawner death. Returns *cmd* unchanged (as a ``list``) when ``enabled`` is
    False, off Linux, or the probe fails — logging a warning in the
    probe-failure case so an operator who turned this on can tell it silently
    didn't apply.

    See docs/decisions/0010-systemd-scope-defense-in-depth.md for the full
    reliability caveats (WSL2 systemd requirement, ``loginctl enable-linger``,
    and why ``RuntimeMaxSec`` is not the ceiling of record).
    """
    if not enabled:
        return list(cmd)
    if not probe_systemd_scope_available():
        logger.warning("systemd_scope_probe_failed", cmd=list(cmd[:1]))
        return list(cmd)
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        f"RuntimeMaxSec={int(ceiling_seconds)}",
        *cmd,
    ]
