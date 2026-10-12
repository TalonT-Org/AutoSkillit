"""Pipeline tracker authority and lifetime ownership.

Every tracker operation obeys one universal lock order: acquire the tracker's
shared or exclusive artifact lease first, then acquire ``.pipeline_tracker.lock``.
The lease sidecar is stable process-lifetime evidence and is never unlinked.
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import psutil

from ..io.io import atomic_write
from ..plugins._active_kitchens import (
    ActiveKitchensState,
    kitchen_entry_alive,
    read_active_kitchens_registry,
)
from ..runtime.artifact_lease import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    ArtifactLease,
    acquire_flock_with_timeout,
)
from ..types import SKILL_TOOLS, FinalizedRecipeStep

__all__ = [
    "TrackerAuthorityReadResult",
    "TrackerAuthorityTarget",
    "TrackerDependencyAdmissionError",
    "TrackerDependencyViolation",
    "TrackerData",
    "TrackerInitialization",
    "TrackerMutation",
    "TrackerOwnerKind",
    "TrackerParticipantKey",
    "dependency_cycle_members",
    "is_tracker_enforced_step",
    "initialize_kitchen_tracker",
    "initialize_manual_tracker",
    "mutate_tracker",
    "pipeline_tracker_directory",
    "pipeline_tracker_path",
    "read_tracker_authority",
    "release_tracker_lease",
    "retain_tracker_lease",
    "tracker_lease_path",
    "try_retire_tracker",
]

TrackerOwnerKind = Literal["kitchen", "dispatch", "manual"]
TrackerData = dict[str, Any]
TrackerMutation = Callable[[TrackerData], TrackerData]


def is_tracker_enforced_step(step: object) -> bool:
    """Only run_skill steps are dependency-gated and earn completion credit."""
    return isinstance(step, FinalizedRecipeStep) and step.tool in SKILL_TOOLS


def _dependency_cycle_edges(
    graph: Mapping[str, Iterable[str]],
) -> set[tuple[str, str]]:
    """Return only edges witnessed along DFS cycle paths and back edges."""
    white, gray, black = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(graph, white)
    cycle_edges: set[tuple[str, str]] = set()

    def visit(node: str, stack: list[str]) -> None:
        color[node] = gray
        stack.append(node)
        for neighbor in graph.get(node, ()):
            if color.get(neighbor) == gray:
                cycle_start = stack.index(neighbor)
                cycle_edges.update(zip(stack[cycle_start:-1], stack[cycle_start + 1 :]))
                cycle_edges.add((node, neighbor))
            elif color.get(neighbor) == white:
                visit(neighbor, stack)
        stack.pop()
        color[node] = black

    for node in graph:
        if color[node] == white:
            visit(node, [])
    return cycle_edges


def dependency_cycle_members(graph: Mapping[str, Iterable[str]]) -> set[str]:
    """Return the step names on witnessed cycle edges in *graph*."""
    return {name for edge in _dependency_cycle_edges(graph) for name in edge}


@dataclass(frozen=True, slots=True)
class TrackerDependencyViolation:
    dependent: str
    prerequisite: str
    reason: Literal["unknown_step", "not_run_skill", "self_dependency", "cycle"]
    detail: str


class TrackerDependencyAdmissionError(ValueError):
    """A dependency graph contains edges the tracker cannot enforce."""

    violations: tuple[TrackerDependencyViolation, ...]

    def __init__(self, violations: Sequence[TrackerDependencyViolation]) -> None:
        self.violations = tuple(violations)
        super().__init__("; ".join(violation.detail for violation in self.violations))


def _normalize_tracker_dependencies(
    dependencies: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    return {
        dependent: tuple(dict.fromkeys(prerequisites))
        for dependent, prerequisites in dependencies.items()
        if prerequisites
    }


def _dependency_step_kind(step: object) -> str:
    if not isinstance(step, FinalizedRecipeStep):
        return "untyped"
    return step.tool or step.action or "unconfigured"


def _dependency_endpoint_violation(
    dependent: str,
    prerequisite: str,
    step_name: str,
    role: str,
    recipe_steps: Mapping[str, FinalizedRecipeStep],
) -> TrackerDependencyViolation | None:
    if step_name not in recipe_steps:
        return TrackerDependencyViolation(
            dependent,
            prerequisite,
            "unknown_step",
            f"{role} '{step_name}' is not a step in the finalized recipe",
        )
    step = recipe_steps[step_name]
    if is_tracker_enforced_step(step):
        return None
    kind = _dependency_step_kind(step)
    description = (
        f"{role} '{step_name}' is untyped"
        if kind == "untyped"
        else f"{role} '{step_name}' is a {kind} step"
    )
    rule = "are dependency-gated" if role == "dependent" else "earn completion credit"
    return TrackerDependencyViolation(
        dependent,
        prerequisite,
        "not_run_skill",
        f"{description}; only run_skill steps {rule}",
    )


def _dependency_endpoint_violations(
    dependencies: Mapping[str, Sequence[str]],
    recipe_steps: Mapping[str, FinalizedRecipeStep],
) -> tuple[list[TrackerDependencyViolation], dict[str, list[str]]]:
    violations: list[TrackerDependencyViolation] = []
    admissible_graph: dict[str, list[str]] = {}
    for dependent, prerequisites in dependencies.items():
        for prerequisite in prerequisites:
            if dependent == prerequisite:
                kind = (
                    _dependency_step_kind(recipe_steps[dependent])
                    if dependent in recipe_steps
                    else "unknown"
                )
                violations.append(
                    TrackerDependencyViolation(
                        dependent,
                        prerequisite,
                        "self_dependency",
                        f"self-dependency: dependent/prerequisite '{dependent}' "
                        f"(kind: {kind}) "
                        "cannot be the same step",
                    )
                )
                continue
            violation_count = len(violations)
            for step_name, role in (
                (dependent, "dependent"),
                (prerequisite, "prerequisite"),
            ):
                violation = _dependency_endpoint_violation(
                    dependent,
                    prerequisite,
                    step_name,
                    role,
                    recipe_steps,
                )
                if violation is not None:
                    violations.append(violation)
            if len(violations) == violation_count:
                admissible_graph.setdefault(dependent, []).append(prerequisite)
                admissible_graph.setdefault(prerequisite, [])
    return violations, admissible_graph


def _dependency_cycle_violations(
    graph: Mapping[str, Iterable[str]],
) -> list[TrackerDependencyViolation]:
    return [
        TrackerDependencyViolation(
            dependent,
            prerequisite,
            "cycle",
            f"dependent '{dependent}' and prerequisite '{prerequisite}' "
            "are run_skill steps in a cycle",
        )
        for dependent, prerequisite in sorted(_dependency_cycle_edges(graph))
    ]


@dataclass(frozen=True, slots=True)
class TrackerInitialization:
    kitchen_id: str
    recipe_steps: Mapping[str, FinalizedRecipeStep]
    dependencies: Mapping[str, Sequence[str]] = field(default_factory=dict)
    skipped: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        recipe_steps = dict(self.recipe_steps)
        dependencies = _normalize_tracker_dependencies(self.dependencies)
        endpoint_violations, admissible_graph = _dependency_endpoint_violations(
            dependencies, recipe_steps
        )
        violations = endpoint_violations + _dependency_cycle_violations(admissible_graph)
        if violations:
            raise TrackerDependencyAdmissionError(violations)
        object.__setattr__(self, "recipe_steps", MappingProxyType(recipe_steps))
        object.__setattr__(self, "dependencies", MappingProxyType(dependencies))
        object.__setattr__(self, "skipped", frozenset(self.skipped))

    def tracker_data(self, *, pipeline_id: str) -> TrackerData:
        return {
            "pipeline_id": pipeline_id,
            "kitchen_id": self.kitchen_id,
            "initialized_at": datetime.now(UTC).isoformat(),
            "tracker_incarnation_id": uuid.uuid4().hex,
            "steps": {
                name: {"status": "skipped" if name in self.skipped else "pending"}
                for name in self.recipe_steps
            },
            "dependencies": {
                dependent: list(prerequisites)
                for dependent, prerequisites in self.dependencies.items()
            },
        }


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
    project_dir: Path

    def __post_init__(self) -> None:
        _validate_target_order_id(self.target_order_id)
        if type(self.expected) is not bool:
            raise ValueError("expected must be a boolean")
        project_dir = Path(self.project_dir)
        path = Path(self.path)
        if path != pipeline_tracker_path(project_dir, self.target_order_id):
            raise ValueError("path must be the project target tracker JSON")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "project_dir", project_dir)

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
            project_dir=project_dir,
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
    lease = ArtifactLease.acquire_shared(
        tracker_lease_path(key.target),
        timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
    )
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
            acquire_flock_with_timeout(
                fd,
                operation=fcntl.LOCK_EX,
                timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
                path=self._lock_path,
            )
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def __exit__(self, *_: object) -> None:
        fd = self._fd
        self._fd = None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)


def _validate_tracker_data(raw: object) -> TrackerData:
    if not isinstance(raw, dict):
        raise ValueError("tracker root must be a JSON object")
    steps = raw.get("steps")
    dependencies = raw.get("dependencies")
    if not isinstance(steps, dict):
        raise ValueError("tracker steps must be a JSON object")
    for step_name, state in steps.items():
        if not isinstance(step_name, str):
            raise ValueError(f"tracker step name {step_name!r} must be a string")
        if not isinstance(state, dict):
            raise ValueError(f"tracker step '{step_name}' must be a JSON object")
    if not isinstance(dependencies, dict):
        raise ValueError("tracker dependencies must be a JSON object")
    for dep_name, prereqs in dependencies.items():
        if (
            not isinstance(dep_name, str)
            or not isinstance(prereqs, list)
            or not all(isinstance(p, str) for p in prereqs)
        ):
            raise ValueError(
                f"tracker dependency entry for {dep_name!r} must map a string step "
                "name to a list of string step names"
            )
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
                    f"{target.path}. Create it with "
                    f"record_pipeline_step(op='init', pipeline_id='{target.target_order_id}') "
                    "(dependencies are optional) or restore that exact tracker, then retry."
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
    initialization: TrackerInitialization,
) -> TrackerAuthorityReadResult:
    """Create or idempotently merge readable kitchen tracker authority."""
    _require_shared_lease(target, lease)
    if not isinstance(initialization, TrackerInitialization):
        raise TypeError(
            f"initialization must be a TrackerInitialization; got {type(initialization).__name__}"
        )
    if initialization.kitchen_id != target.target_order_id:
        raise ValueError(
            f"kitchen_id {initialization.kitchen_id!r} must match "
            f"the tracker target id {target.target_order_id!r}"
        )
    incoming = _validate_tracker_data(
        initialization.tracker_data(pipeline_id=target.target_order_id)
    )
    with _TrackerLock(target):
        current = _read_tracker_unlocked(target)
        if current.error is not None:
            return current
        merged = dict(incoming)
        if current.data is not None:
            merged.update(current.data)
            # Step statuses are observed execution state and therefore existing-wins.
            # Dependencies are recipe-derived state and must be replaced on each serve.
            merged["steps"] = {**incoming["steps"], **current.data["steps"]}
            merged["dependencies"] = incoming["dependencies"]
        target.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        atomic_write(target.path, json.dumps(merged))
        return TrackerAuthorityReadResult(target, data=merged)


def initialize_manual_tracker(
    target: TrackerAuthorityTarget,
    lease: ArtifactLease,
    initialization: TrackerInitialization,
) -> TrackerAuthorityReadResult:
    """Create manual tracker authority, refusing every existing target."""
    _require_shared_lease(target, lease)
    if not isinstance(initialization, TrackerInitialization):
        raise TypeError(
            f"initialization must be a TrackerInitialization; got {type(initialization).__name__}"
        )
    incoming = _validate_tracker_data(
        initialization.tracker_data(pipeline_id=target.target_order_id)
    )
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
        updated = _validate_tracker_data(mutation(deepcopy(current.data)))
        if updated["dependencies"] != current.data["dependencies"]:
            raise ValueError("tracker mutations must not change dependencies")
        if updated == current.data:
            return current
        atomic_write(target.path, json.dumps(updated))
        return TrackerAuthorityReadResult(target, data=updated)


def try_retire_tracker(target: TrackerAuthorityTarget) -> bool:
    """Retire exactly one unleased, valid tracker after fresh liveness checks."""
    try:
        exclusive = ArtifactLease.acquire_exclusive(
            tracker_lease_path(target),
            timeout=0.0,
        )
    except (OSError, RuntimeError):
        return False
    try:
        with _TrackerLock(target):
            current = _read_tracker_unlocked(target)
            if current.data is None:
                return False
            try:
                active_kitchens = read_active_kitchens_registry()
                if active_kitchens.state is not ActiveKitchensState.EXACT:
                    return False
                kitchen_id = current.data.get("kitchen_id")
                if not isinstance(kitchen_id, str) or not kitchen_id:
                    return False
                project_path = str(target.project_dir.resolve())
                matching = [
                    entry
                    for entry in active_kitchens.entries
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
