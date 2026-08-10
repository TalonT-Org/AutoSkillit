"""Pipeline tracker authority and lifetime ownership.

Every tracker operation obeys one universal lock order: acquire the tracker's
shared or exclusive artifact lease first, then acquire ``.pipeline_tracker.lock``.
The lease sidecar is stable process-lifetime evidence and is never unlinked.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import psutil

from ._plugin_cache import kitchen_entry_alive, read_active_kitchens_registry
from .io import atomic_write
from .runtime.artifact_lease import ArtifactLease

TrackerOwnerKind = Literal["kitchen", "dispatch", "manual"]
TrackerData = dict[str, Any]
TrackerMutation = Callable[[TrackerData], TrackerData]

_TRACKER_DIR_COMPONENTS = (".autoskillit", "temp", "pipeline_tracker")


def pipeline_tracker_directory(project_dir: Path) -> Path:
    """Return the single tracker directory for *project_dir*."""
    return Path(project_dir).joinpath(*_TRACKER_DIR_COMPONENTS)


def pipeline_tracker_path(project_dir: Path, target_order_id: str) -> Path:
    """Return the tracker JSON path for one validated explicit target."""
    _validate_target_order_id(target_order_id)
    return pipeline_tracker_directory(project_dir) / f"{target_order_id}.json"


def _validate_target_order_id(target_order_id: str) -> None:
    if not isinstance(target_order_id, str) or not target_order_id:
        raise ValueError("target_order_id must be a nonempty string")
    if (
        target_order_id in {".", ".."}
        or Path(target_order_id).name != target_order_id
        or "/" in target_order_id
        or "\\" in target_order_id
        or "\x00" in target_order_id
    ):
        raise ValueError("target_order_id must be path-safe")


@dataclass(frozen=True, slots=True)
class TrackerAuthorityTarget:
    """One caller-selected tracker authority target."""

    target_order_id: str
    path: Path
    expected: bool

    def __post_init__(self) -> None:
        _validate_target_order_id(self.target_order_id)
        if type(self.expected) is not bool:
            raise ValueError("expected must be a boolean")
        path = Path(self.path)
        if path.name != f"{self.target_order_id}.json":
            raise ValueError("path must name the explicit target tracker JSON")
        object.__setattr__(self, "path", path)

    @classmethod
    def for_project(
        cls,
        project_dir: Path,
        target_order_id: str,
        *,
        expected: bool,
    ) -> TrackerAuthorityTarget:
        return cls(
            target_order_id=target_order_id,
            path=pipeline_tracker_path(project_dir, target_order_id),
            expected=expected,
        )


@dataclass(frozen=True, slots=True)
class TrackerAuthorityReadResult:
    """A tracker read that cannot erase expected authority failures."""

    target: TrackerAuthorityTarget
    data: TrackerData | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, TrackerAuthorityTarget):
            raise ValueError("target must be a TrackerAuthorityTarget")
        if self.data is not None and self.error is not None:
            raise ValueError("parsed tracker data and error cannot coexist")
        if self.error is not None and not self.error.strip():
            raise ValueError("tracker authority errors must be actionable")
        if self.target.expected and self.data is None and self.error is None:
            raise ValueError("unavailable expected authority requires an error")

    @property
    def target_order_id(self) -> str:
        return self.target.target_order_id

    @property
    def path(self) -> Path:
        return self.target.path

    @property
    def expected(self) -> bool:
        return self.target.expected


@dataclass(frozen=True, slots=True)
class TrackerParticipantKey:
    """Exact identity of one retained tracker participant."""

    target: TrackerAuthorityTarget
    owner_kind: TrackerOwnerKind
    owner_id: str
    pid: int
    create_time: float
    project_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, TrackerAuthorityTarget):
            raise ValueError("target must be a TrackerAuthorityTarget")
        if self.owner_kind not in ("kitchen", "dispatch", "manual"):
            raise ValueError("owner_kind must be kitchen, dispatch, or manual")
        if not isinstance(self.owner_id, str) or not self.owner_id:
            raise ValueError("owner_id must be a nonempty string")
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise ValueError("pid must be a positive integer")
        if (
            isinstance(self.create_time, bool)
            or not isinstance(self.create_time, (int, float))
            or self.create_time <= 0
        ):
            raise ValueError("create_time must be a positive number")
        if not isinstance(self.project_path, str) or not self.project_path:
            raise ValueError("project_path must be a nonempty string")


def tracker_lease_path(target: TrackerAuthorityTarget) -> Path:
    """Return the stable physical lease sidecar for *target*."""
    return target.path.with_suffix(".lease.lock")


def retain_tracker_lease(
    leases: MutableMapping[TrackerParticipantKey, ArtifactLease],
    key: TrackerParticipantKey,
) -> ArtifactLease:
    """Retain one shared descriptor under its complete participant key."""
    current = leases.get(key)
    if current is not None and not current.closed:
        return current
    lease = ArtifactLease.acquire_shared(tracker_lease_path(key.target))
    leases[key] = lease
    return lease


def release_tracker_lease(
    leases: MutableMapping[TrackerParticipantKey, ArtifactLease],
    key: TrackerParticipantKey,
) -> None:
    """Release only the descriptor owned by *key*."""
    lease = leases.pop(key, None)
    if lease is not None:
        lease.close_preserving()


def _require_shared_lease(target: TrackerAuthorityTarget, lease: ArtifactLease) -> None:
    if lease.closed or not lease.shared or lease.path != tracker_lease_path(target):
        raise ValueError("operation requires the target's retained shared tracker lease")


class _TrackerLock:
    def __init__(self, target: TrackerAuthorityTarget) -> None:
        self._lock_path = target.path.parent / ".pipeline_tracker.lock"
        self._fd: int | None = None

    def __enter__(self) -> None:
        self._lock_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def __exit__(self, *_: object) -> None:
        fd = self._fd
        self._fd = None
        if fd is not None:
            os.close(fd)


def _validate_tracker_data(raw: object) -> TrackerData:
    if not isinstance(raw, dict):
        raise ValueError("tracker root must be a JSON object")
    steps = raw.get("steps")
    dependencies = raw.get("dependencies")
    if not isinstance(steps, dict):
        raise ValueError("tracker steps must be a JSON object")
    if not isinstance(dependencies, dict):
        raise ValueError("tracker dependencies must be a JSON object")
    return dict(raw)


def _read_tracker_unlocked(target: TrackerAuthorityTarget) -> TrackerAuthorityReadResult:
    try:
        raw_bytes = target.path.read_bytes()
    except FileNotFoundError:
        if target.expected:
            return TrackerAuthorityReadResult(
                target,
                error=(
                    f"Expected pipeline tracker '{target.target_order_id}' is unavailable at "
                    f"{target.path}. Initialize or restore that exact tracker before retrying."
                ),
            )
        return TrackerAuthorityReadResult(target)
    except OSError as exc:
        return TrackerAuthorityReadResult(
            target,
            error=(
                f"Cannot read pipeline tracker '{target.target_order_id}' at {target.path}: {exc}"
            ),
        )
    try:
        parsed = json.loads(raw_bytes)
        data = _validate_tracker_data(parsed)
    except ValueError as exc:
        return TrackerAuthorityReadResult(
            target,
            error=(
                f"Pipeline tracker '{target.target_order_id}' at {target.path} is invalid: {exc}. "
                "Repair or restore that exact tracker before retrying."
            ),
        )
    return TrackerAuthorityReadResult(target, data=data)


def read_tracker_authority(
    target: TrackerAuthorityTarget,
    lease: ArtifactLease,
) -> TrackerAuthorityReadResult:
    """Read and validate one authority target under its retained shared lease."""
    _require_shared_lease(target, lease)
    with _TrackerLock(target):
        return _read_tracker_unlocked(target)


def initialize_kitchen_tracker(
    target: TrackerAuthorityTarget,
    lease: ArtifactLease,
    data: Mapping[str, Any],
) -> TrackerAuthorityReadResult:
    """Create or idempotently merge readable kitchen tracker authority."""
    _require_shared_lease(target, lease)
    incoming = _validate_tracker_data(dict(data))
    with _TrackerLock(target):
        current = _read_tracker_unlocked(target)
        if current.error is not None:
            return current
        merged = dict(incoming)
        if current.data is not None:
            merged.update(current.data)
            merged["steps"] = {**incoming["steps"], **current.data["steps"]}
            merged["dependencies"] = {
                **incoming["dependencies"],
                **current.data["dependencies"],
            }
        target.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        atomic_write(target.path, json.dumps(merged))
        return TrackerAuthorityReadResult(target, data=merged)


def initialize_manual_tracker(
    target: TrackerAuthorityTarget,
    lease: ArtifactLease,
    data: Mapping[str, Any],
) -> TrackerAuthorityReadResult:
    """Create manual tracker authority, refusing every existing target."""
    _require_shared_lease(target, lease)
    incoming = _validate_tracker_data(dict(data))
    with _TrackerLock(target):
        if target.path.exists() or target.path.is_symlink():
            return TrackerAuthorityReadResult(
                target,
                error=(
                    f"Pipeline tracker '{target.target_order_id}' already exists at "
                    f"{target.path}; manual initialization is create-only."
                ),
            )
        target.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        atomic_write(target.path, json.dumps(incoming))
        return TrackerAuthorityReadResult(target, data=incoming)


def mutate_tracker(
    target: TrackerAuthorityTarget,
    lease: ArtifactLease,
    mutation: TrackerMutation,
) -> TrackerAuthorityReadResult:
    """Fresh-read, validate, mutate, and atomically replace one tracker."""
    _require_shared_lease(target, lease)
    with _TrackerLock(target):
        current = _read_tracker_unlocked(target)
        if current.data is None:
            return current
        updated = _validate_tracker_data(mutation(dict(current.data)))
        atomic_write(target.path, json.dumps(updated))
        return TrackerAuthorityReadResult(target, data=updated)


def try_retire_tracker(target: TrackerAuthorityTarget) -> bool:
    """Retire exactly one unleased, valid tracker after fresh liveness checks."""
    try:
        exclusive = ArtifactLease.acquire_exclusive(tracker_lease_path(target), blocking=False)
    except (OSError, RuntimeError):
        return False
    try:
        with _TrackerLock(target):
            current = _read_tracker_unlocked(target)
            if current.data is None:
                return False
            try:
                entries = read_active_kitchens_registry()
                kitchen_id = current.data.get("kitchen_id")
                if not isinstance(kitchen_id, str) or not kitchen_id:
                    return False
                # The tracker JSON lives four levels below the project root.
                project_path = str(target.path.parents[3].resolve())
                matching = [
                    entry
                    for entry in entries
                    if entry.get("kitchen_id") == kitchen_id
                    and str(Path(str(entry.get("project_path"))).resolve()) == project_path
                ]
                if any(kitchen_entry_alive(entry) for entry in matching):
                    return False
                target.path.unlink()
            except (OSError, ValueError, json.JSONDecodeError, psutil.Error):
                return False
        return True
    except (OSError, RuntimeError):
        return False
    finally:
        exclusive.close_preserving()
