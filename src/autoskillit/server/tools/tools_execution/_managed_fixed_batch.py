"""Server-owned execution and recovery for one immutable managed fixed batch.

This module deliberately has no MCP decorator.  A later handler resolves a
trusted launch binding and calls :meth:`ManagedFixedBatchService.run`; native
join declaration remains a separate backend capability route.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from autoskillit.core import (
    BackgroundSupervisor,
    ManagedWorkerCapacity,
    ManagedWorkerPermit,
    SkillContractError,
    WriteBehaviorSpec,
    get_logger,
    read_versioned_json,
    write_canonical_versioned_json,
    write_versioned_json,
)
from autoskillit.hooks import (
    OUTCOME_CANCELLED,
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_INTERRUPTED,
    OUTCOME_LAUNCH_FAILED,
    OUTCOME_REAPED,
    JoinLedgerError,
    active_batch,
    admit_assignment,
    aggregate_batch,
    cancel_batch,
    mark_assignment_running,
    open_or_replay,
    reconcile_batch,
    settle_assignment,
)
from autoskillit.server.tools.tools_execution._managed_leaf import (
    ManagedLeafAssignmentInput,
    ManagedLeafProjection,
    bind_managed_leaf,
    plan_managed_leaf_identities,
    project_managed_leaf,
)

logger = get_logger(__name__)

_TERMINAL_OUTCOMES = frozenset(
    {
        OUTCOME_CANCELLED,
        OUTCOME_COMPLETED,
        OUTCOME_FAILED,
        OUTCOME_INTERRUPTED,
        OUTCOME_LAUNCH_FAILED,
        OUTCOME_REAPED,
    }
)
_RECOVERY_SCHEMA_VERSION = 1
_RESULT_SCHEMA_VERSION = 1
_RESULT_REFERENCE_PREFIX = "fixed-batch-result-"


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ManagedLaunchBinding:
    """Immutable trusted launch facts resolved before a managed batch opens."""

    request_session_id: str
    managed_parent_id: str
    parent_session_id: str
    caller_key: str
    attestation_epoch: int
    recovery_ready: bool
    selected_source: object

    def __post_init__(self) -> None:
        for name in (
            "request_session_id",
            "managed_parent_id",
            "parent_session_id",
            "caller_key",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise SkillContractError(f"managed launch binding {name} must be non-empty")
        if type(self.attestation_epoch) is not int or self.attestation_epoch < 0:
            raise SkillContractError("managed launch binding attestation_epoch is invalid")
        if self.selected_source is None:
            raise SkillContractError("managed launch binding requires selected source evidence")


@runtime_checkable
class ManagedLaunchResolver(Protocol):
    """Resolve caller input into server-owned immutable managed launch evidence."""

    def resolve(self, request: object) -> ManagedLaunchBinding: ...


@dataclass(frozen=True, slots=True)
class ManagedLeafLaunchResult:
    """Bounded terminal facts produced by the leaf launch adapter."""

    outcome: str = OUTCOME_COMPLETED
    backend_session_id: str = ""
    result_reference: str | None = None
    result_digest: str | None = None
    result_payload: object | None = None
    cleanup_outcome: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in _TERMINAL_OUTCOMES:
            raise ValueError(f"unsupported managed leaf terminal outcome {self.outcome!r}")
        if self.cleanup_outcome not in {None, OUTCOME_REAPED}:
            raise ValueError("managed leaf cleanup outcome must be reaped or absent")


ManagedLeafLauncher = Callable[
    [ManagedLeafProjection, ManagedWorkerPermit], Awaitable[ManagedLeafLaunchResult]
]


@dataclass(frozen=True, slots=True)
class ManagedFixedBatchLaunchBinding:
    """All server-resolved facts needed to run one complete fixed assignment set."""

    launch: ManagedLaunchBinding
    flag_dir: Path
    source_document: object
    adaptation: object
    assignments: tuple[ManagedLeafAssignmentInput, ...]
    default_model: str
    write_behavior: WriteBehaviorSpec
    read_only: bool
    launch_leaf: ManagedLeafLauncher

    def __post_init__(self) -> None:
        if not self.launch.recovery_ready:
            raise SkillContractError("managed launch binding is blocked by recovery")
        if not isinstance(self.flag_dir, Path):
            raise SkillContractError("managed fixed batch requires a channel directory")
        if not self.assignments:
            raise SkillContractError("managed fixed batch requires at least one assignment")
        if not isinstance(self.default_model, str) or not self.default_model:
            raise SkillContractError("managed fixed batch default_model must be non-empty")


@runtime_checkable
class ManagedFixedBatchLaunchResolver(Protocol):
    """Only a resolver may transform a request into a launchable batch binding."""

    def resolve(self, request: object) -> ManagedFixedBatchLaunchBinding: ...


@dataclass(frozen=True, slots=True)
class ManagedFixedBatchResult:
    """Replay-safe batch identity and aggregate state returned by the supervisor."""

    batch_id: str
    wave_outcome: str
    replayed: bool
    result_reference: str | None = None
    result_digest: str | None = None


def _result_reference(batch_id: str, assignment_id: str) -> str:
    component = assignment_id or "aggregate"
    return f"{_RESULT_REFERENCE_PREFIX}{batch_id}-{component}"


def _write_fixed_batch_result(path: Path, payload: dict[str, object]) -> None:
    """Atomically register one immutable, relocatable result record."""
    write_canonical_versioned_json(
        path,
        payload,
        _RESULT_SCHEMA_VERSION,
        exclusive=True,
    )


class ManagedFixedBatchResultStore:
    """Persist opaque fixed-batch result bytes with scope-bound reads."""

    def __init__(self, state_root: Path) -> None:
        self._result_dir = state_root / "results"

    def publish(
        self,
        *,
        launch: ManagedLaunchBinding,
        batch_id: str,
        assignment_id: str,
        payload: object,
    ) -> tuple[str, str]:
        reference = _result_reference(batch_id, assignment_id)
        digest = _digest(payload)
        path = self._path(reference)
        record = {
            "result_reference": reference,
            "result_digest": digest,
            "request_session_id": launch.request_session_id,
            "managed_parent_id": launch.managed_parent_id,
            "source_artifact_digest": getattr(
                launch.selected_source, "source_artifact_digest", ""
            ),
            "source_artifact_incarnation_id": getattr(
                launch.selected_source, "source_artifact_incarnation_id", ""
            ),
            "batch_id": batch_id,
            "assignment_id": assignment_id,
            "payload": payload,
        }
        self._result_dir.mkdir(parents=True, exist_ok=True)
        try:
            _write_fixed_batch_result(path, record)
        except FileExistsError:
            existing = self._load(reference)
            if existing is None or _canonical(existing) != _canonical(
                {**record, "schema_version": _RESULT_SCHEMA_VERSION}
            ):
                raise SkillContractError("managed fixed-batch result reference conflicts")
        return reference, digest

    def read(
        self,
        *,
        reference: str,
        launch: ManagedLaunchBinding,
        batch_id: str,
        assignment_id: str,
    ) -> object:
        record = self._load(reference)
        if record is None:
            raise SkillContractError("managed fixed-batch result is unavailable")
        expected = {
            "result_reference": reference,
            "request_session_id": launch.request_session_id,
            "managed_parent_id": launch.managed_parent_id,
            "source_artifact_digest": getattr(
                launch.selected_source, "source_artifact_digest", ""
            ),
            "source_artifact_incarnation_id": getattr(
                launch.selected_source, "source_artifact_incarnation_id", ""
            ),
            "batch_id": batch_id,
            "assignment_id": assignment_id,
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise SkillContractError("managed fixed-batch result authorization failed")
        payload = record.get("payload")
        if _digest(payload) != record.get("result_digest"):
            raise SkillContractError("managed fixed-batch result digest is invalid")
        return payload

    def _load(self, reference: str) -> dict[str, object] | None:
        if not reference.startswith(_RESULT_REFERENCE_PREFIX):
            return None
        record = read_versioned_json(
            self._path(reference),
            _RESULT_SCHEMA_VERSION,
            raise_io_errors=True,
        )
        return record if isinstance(record, dict) else None

    def _path(self, reference: str) -> Path:
        return self._result_dir / f"{hashlib.sha256(reference.encode()).hexdigest()}.json"


@dataclass(frozen=True, slots=True)
class _RecoveryDebt:
    owner: tuple[str, str, str]
    permit_id: str
    flag_dir: str
    request_session_id: str
    managed_parent_id: str
    batch_id: str
    assignment_id: str
    attempt_id: str
    run_id: str


RecoveryVerifier = Callable[[_RecoveryDebt], Awaitable[bool | None]]


class ManagedFixedBatchService:
    """Own fixed-batch scheduling, capacity debt, settlement, and recovery gating."""

    def __init__(
        self,
        *,
        capacity: ManagedWorkerCapacity,
        background: BackgroundSupervisor,
        state_root: Path,
        recovery_verifier: RecoveryVerifier | None = None,
        cancel_timeout: float = 5.0,
    ) -> None:
        self._capacity = capacity
        self._background = background
        self._temp_state_root = state_root
        self._state_path = state_root / "recovery.json"
        self._result_store = ManagedFixedBatchResultStore(state_root)
        self._recovery_verifier = recovery_verifier
        self._cancel_timeout = cancel_timeout
        self._recovery_ready = False
        self._recovery_diagnostic = "managed recovery has not completed"
        self._tasks: dict[str, asyncio.Task[ManagedFixedBatchResult]] = {}
        self._debt: dict[str, _RecoveryDebt] = {}
        self._lock = asyncio.Lock()

    @property
    def recovery_ready(self) -> bool:
        return self._recovery_ready

    @property
    def recovery_diagnostic(self) -> str:
        return self._recovery_diagnostic

    async def reconcile_startup(self) -> bool:
        """Restore capacity debt, then fail closed until every owner is verified."""
        async with self._lock:
            try:
                self._debt = self._read_debt()
            except SkillContractError as exc:
                self._recovery_ready = False
                self._recovery_diagnostic = str(exc)
                return False
            recovered: dict[str, ManagedWorkerPermit] = {}
            for permit_id, debt in self._debt.items():
                try:
                    recovered[permit_id] = self._capacity.restore_owner_debt(debt.owner, permit_id)
                except Exception as exc:
                    logger.warning(
                        "managed_capacity_debt_restore_failed",
                        permit_id=permit_id,
                        exc_info=True,
                    )
                    self._recovery_ready = False
                    self._recovery_diagnostic = f"managed capacity debt restore failed: {exc}"
                    return False
            for permit_id, debt in tuple(self._debt.items()):
                verified_absent = await self._verified_absent(debt)
                if verified_absent is not True:
                    self._recovery_ready = False
                    self._recovery_diagnostic = (
                        "unresolved managed worker debt; retry recovery after verifying "
                        f"process/session identity for {debt.assignment_id}"
                    )
                    self._write_debt()
                    return False
                try:
                    settle_assignment(
                        Path(debt.flag_dir),
                        session_id=debt.request_session_id,
                        top_level_parent=debt.managed_parent_id,
                        tool_use_id=debt.assignment_id,
                        outcome=OUTCOME_REAPED,
                        batch_id=debt.batch_id,
                        assignment_id=debt.assignment_id,
                        attempt_id=debt.attempt_id,
                        run_id=debt.run_id,
                        terminal_event_id=f"recovery-reaped:{debt.run_id}",
                        terminal_payload_digest=_digest(asdict(debt)),
                        cleanup_outcome=OUTCOME_REAPED,
                    )
                except JoinLedgerError:
                    self._recovery_ready = False
                    self._recovery_diagnostic = "managed ledger settlement could not be verified"
                    self._write_debt()
                    return False
                self._capacity.release(recovered[permit_id])
                del self._debt[permit_id]
            self._write_debt()
            self._recovery_ready = True
            self._recovery_diagnostic = ""
            return True

    async def run(self, binding: ManagedFixedBatchLaunchBinding) -> ManagedFixedBatchResult:
        """Open/replay first, then supervise only the immutable declared members."""
        if not self._recovery_ready or not binding.launch.recovery_ready:
            raise SkillContractError(
                self._recovery_diagnostic or "managed fixed batches are blocked by recovery"
            )
        plan = plan_managed_leaf_identities(binding.launch.caller_key, binding.assignments)
        declaration = {"assignments": [dict(item.ledger_membership) for item in plan.assignments]}
        batch = open_or_replay(
            binding.flag_dir,
            parent={
                "request_session_id": binding.launch.request_session_id,
                "managed_parent_id": binding.launch.managed_parent_id,
                "managed_leaf_id": "",
            },
            selected_source={
                "skill_name": getattr(binding.launch.selected_source, "skill_name", ""),
                "source_artifact_digest": getattr(
                    binding.launch.selected_source, "source_artifact_digest", ""
                ),
                "source_artifact_incarnation_id": getattr(
                    binding.launch.selected_source, "source_artifact_incarnation_id", ""
                ),
            },
            key=binding.launch.caller_key,
            declaration=declaration,
        )
        batch_id = str(batch["join_batch_id"])
        if batch.get("wave_outcome") != "pending":
            return self._published_batch_result(
                binding,
                batch_id,
                str(batch["wave_outcome"]),
                replayed=True,
            )
        async with self._lock:
            task = self._tasks.get(batch_id)
            if task is None:
                task = self._background.submit(
                    self._supervise(binding, plan, batch),
                    label=f"managed-fixed-batch:{batch_id}",
                )
                self._tasks[batch_id] = task
                replayed = False
            else:
                replayed = True
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            await self._cancel_and_join(batch_id, binding, batch)
            raise
        finally:
            if task.done():
                async with self._lock:
                    self._tasks.pop(batch_id, None)
        return self._published_batch_result(
            binding,
            result.batch_id,
            result.wave_outcome,
            replayed=replayed,
        )

    def read_result(
        self,
        *,
        reference: str,
        launch: ManagedLaunchBinding,
        batch_id: str,
        assignment_id: str = "",
    ) -> object:
        """Load one result only after its immutable managed scope is revalidated."""
        return self._result_store.read(
            reference=reference,
            launch=launch,
            batch_id=batch_id,
            assignment_id=assignment_id,
        )

    async def close(self) -> None:
        """Cancel, bounded-join, and preserve any unresolved durable debt."""
        async with self._lock:
            tasks = tuple(self._tasks.items())
        for _batch_id, task in tasks:
            task.cancel()
        if tasks:
            try:
                pending = asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
                await asyncio.wait_for(asyncio.shield(pending), timeout=self._cancel_timeout)
            except TimeoutError:
                self._recovery_ready = False
                self._recovery_diagnostic = "managed batch close timed out; recovery is required"

    async def _supervise(self, binding, plan, batch) -> ManagedFixedBatchResult:
        batch_id = str(batch["join_batch_id"])
        entered: set[str] = set()
        ledger_assignments = {
            int(item["ordinal"]): str(item["assignment_id"])
            for item in batch["assignments"]
            if isinstance(item, Mapping)
        }
        try:
            async with asyncio.TaskGroup() as group:
                for identity in plan.assignments:
                    group.create_task(
                        self._run_assignment(
                            binding,
                            batch_id,
                            ledger_assignments[identity.ordinal],
                            identity,
                            entered,
                        )
                    )
        except asyncio.CancelledError:
            cancel_batch(
                binding.flag_dir,
                batch_id=batch_id,
                terminal_event_id=f"cancelled:{batch_id}",
            )
            raise
        except BaseException:
            reconcile_batch(
                binding.flag_dir,
                batch_id=batch_id,
                terminal_event_id=f"interrupted:{batch_id}",
            )
            raise
        finally:
            # A cancelled task that never entered has no finally block or permit.
            if len(entered) != len(plan.assignments):
                cancel_batch(
                    binding.flag_dir,
                    batch_id=batch_id,
                    terminal_event_id=f"unlaunched:{batch_id}",
                )
        wave_outcome = aggregate_batch(binding.flag_dir, batch_id=batch_id)
        return ManagedFixedBatchResult(batch_id, wave_outcome, False)

    async def _run_assignment(
        self, binding, batch_id, ledger_assignment_id, identity, entered
    ) -> None:
        entered.add(ledger_assignment_id)
        attempt_id = f"{identity.first_run_id}:attempt-0"
        permit: ManagedWorkerPermit | None = None
        admitted = False
        running = False
        owner = (batch_id, ledger_assignment_id, identity.first_run_id)
        try:
            permit = await self._capacity.acquire(owner)
            projection = project_managed_leaf(
                bind_managed_leaf(
                    assignment=identity,
                    selected_source=binding.launch.selected_source,
                    source_document=binding.source_document,
                    adaptation=binding.adaptation,
                    default_model=binding.default_model,
                    write_behavior=binding.write_behavior,
                    read_only=binding.read_only,
                ),
                binding.source_document,
            )
            debt = _RecoveryDebt(
                owner=owner,
                permit_id=permit.permit_id,
                flag_dir=str(binding.flag_dir),
                request_session_id=binding.launch.request_session_id,
                managed_parent_id=binding.launch.managed_parent_id,
                batch_id=batch_id,
                assignment_id=ledger_assignment_id,
                attempt_id=attempt_id,
                run_id=identity.first_run_id,
            )
            self._debt[permit.permit_id] = debt
            self._write_debt()
            admit_assignment(
                binding.flag_dir,
                batch_id=batch_id,
                assignment_id=ledger_assignment_id,
                attempt_id=attempt_id,
                run_id=identity.first_run_id,
                evidence={**projection.ledger_attempt_evidence, "permit_id": permit.permit_id},
            )
            admitted = True
            mark_assignment_running(
                binding.flag_dir,
                batch_id=batch_id,
                assignment_id=ledger_assignment_id,
                attempt_id=attempt_id,
                run_id=identity.first_run_id,
            )
            running = True
            result = await binding.launch_leaf(projection, permit)
            self._settle(
                binding,
                batch_id,
                ledger_assignment_id,
                attempt_id,
                identity.first_run_id,
                result,
            )
        except asyncio.CancelledError:
            if admitted:
                self._settle(
                    binding,
                    batch_id,
                    ledger_assignment_id,
                    attempt_id,
                    identity.first_run_id,
                    ManagedLeafLaunchResult(outcome=OUTCOME_CANCELLED),
                )
            raise
        except Exception:
            logger.warning(
                "managed_fixed_batch_assignment_failed",
                assignment_id=ledger_assignment_id,
                exc_info=True,
            )
            if admitted:
                self._settle(
                    binding,
                    batch_id,
                    ledger_assignment_id,
                    attempt_id,
                    identity.first_run_id,
                    ManagedLeafLaunchResult(
                        outcome=OUTCOME_FAILED if running else OUTCOME_LAUNCH_FAILED
                    ),
                )
            elif permit is not None:
                # Projection/preparation failed after a permit but before admission.
                cancel_batch(
                    binding.flag_dir,
                    batch_id=batch_id,
                    terminal_event_id=f"launch-failed:{identity.first_run_id}",
                )
        except BaseException:
            if admitted:
                self._settle(
                    binding,
                    batch_id,
                    ledger_assignment_id,
                    attempt_id,
                    identity.first_run_id,
                    ManagedLeafLaunchResult(outcome=OUTCOME_INTERRUPTED),
                )
            raise
        finally:
            if permit is not None:
                self._debt.pop(permit.permit_id, None)
                self._write_debt()
                self._capacity.release(permit)

    def _settle(self, binding, batch_id, assignment_id, attempt_id, run_id, result) -> None:
        if result.result_payload is not None:
            reference, digest = self._result_store.publish(
                launch=binding.launch,
                batch_id=batch_id,
                assignment_id=assignment_id,
                payload=result.result_payload,
            )
            result = replace(result, result_reference=reference, result_digest=digest)
        settle_assignment(
            binding.flag_dir,
            session_id=binding.launch.request_session_id,
            top_level_parent=binding.launch.managed_parent_id,
            tool_use_id=assignment_id,
            outcome=result.outcome,
            batch_id=batch_id,
            assignment_id=assignment_id,
            attempt_id=attempt_id,
            run_id=run_id,
            terminal_event_id=f"terminal:{run_id}",
            terminal_payload_digest=_digest(
                {
                    "outcome": result.outcome,
                    "result_reference": result.result_reference,
                    "result_digest": result.result_digest,
                    "cleanup_outcome": result.cleanup_outcome,
                }
            ),
            result_reference=result.result_reference,
            result_digest=result.result_digest,
            cleanup_outcome=result.cleanup_outcome,
        )

    def _published_batch_result(
        self,
        binding: ManagedFixedBatchLaunchBinding,
        batch_id: str,
        wave_outcome: str,
        *,
        replayed: bool,
    ) -> ManagedFixedBatchResult:
        batch = active_batch(
            binding.flag_dir,
            session_id=binding.launch.request_session_id,
            top_level_parent=binding.launch.managed_parent_id,
        )
        if not isinstance(batch, Mapping) or batch.get("join_batch_id") != batch_id:
            raise SkillContractError("managed fixed-batch ledger snapshot is unavailable")
        assignments = batch.get("assignments")
        if not isinstance(assignments, list):
            raise SkillContractError("managed fixed-batch ledger assignments are malformed")
        aggregate = {
            "batch_id": batch_id,
            "wave_outcome": wave_outcome,
            "assignments": [
                {
                    key: assignment.get(key)
                    for key in (
                        "assignment_id",
                        "ordinal",
                        "role",
                        "label",
                        "runtime_key",
                        "outcome",
                        "result_reference",
                        "result_digest",
                    )
                }
                for assignment in assignments
                if isinstance(assignment, Mapping)
            ],
        }
        reference, digest = self._result_store.publish(
            launch=binding.launch,
            batch_id=batch_id,
            assignment_id="",
            payload=aggregate,
        )
        return ManagedFixedBatchResult(
            batch_id=batch_id,
            wave_outcome=wave_outcome,
            replayed=replayed,
            result_reference=reference,
            result_digest=digest,
        )

    async def _cancel_and_join(self, batch_id, binding, batch) -> None:
        async with self._lock:
            task = self._tasks.get(batch_id)
        if task is not None:
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self._cancel_timeout)
            except (TimeoutError, asyncio.CancelledError):
                pass
        cancel_batch(
            binding.flag_dir,
            batch_id=batch_id,
            terminal_event_id=f"request-cancelled:{batch_id}",
        )

    async def _verified_absent(self, debt: _RecoveryDebt) -> bool | None:
        if self._recovery_verifier is None:
            return None
        try:
            return await self._recovery_verifier(debt)
        except Exception:
            logger.warning("managed_recovery_liveness_check_failed", exc_info=True)
            return None

    def _read_debt(self) -> dict[str, _RecoveryDebt]:
        if not self._state_path.exists():
            return {}
        try:
            payload = read_versioned_json(
                self._state_path,
                _RECOVERY_SCHEMA_VERSION,
                raise_io_errors=True,
            )
            if payload is None:
                raise ValueError("unsupported managed recovery schema")
            return {
                item["permit_id"]: _RecoveryDebt(
                    owner=tuple(item["owner"]),
                    permit_id=item["permit_id"],
                    flag_dir=item["flag_dir"],
                    request_session_id=item["request_session_id"],
                    managed_parent_id=item["managed_parent_id"],
                    batch_id=item["batch_id"],
                    assignment_id=item["assignment_id"],
                    attempt_id=item["attempt_id"],
                    run_id=item["run_id"],
                )
                for item in payload.get("debt", [])
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SkillContractError(f"managed recovery state is unreadable: {exc}") from exc

    def _write_debt(self) -> None:
        write_versioned_json(
            self._temp_state_root / "recovery.json",
            {"debt": [asdict(item) for item in self._debt.values()]},
            _RECOVERY_SCHEMA_VERSION,
        )


__all__ = [
    "ManagedFixedBatchLaunchBinding",
    "ManagedFixedBatchLaunchResolver",
    "ManagedFixedBatchResult",
    "ManagedFixedBatchResultStore",
    "ManagedFixedBatchService",
    "ManagedLaunchBinding",
    "ManagedLaunchResolver",
    "ManagedLeafLaunchResult",
    "_write_fixed_batch_result",
]
