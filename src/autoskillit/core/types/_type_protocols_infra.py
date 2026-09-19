"""Infrastructure and pipeline-control protocol definitions.

This module groups three responsibilities documented together by the core/types
concern map: infrastructure, pipeline-control, and server-owned audit/plan-set
publication protocols. The three publication protocols (AuditAuthorityMaterializer,
CommittedDispositionResolver, PlanSetMaterializer) live in their own section
below for clarity even though they share this file. They were originally split
into _type_audit_protocols.py but were consolidated here to stay within the
core/types file-count budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .._managed_worker_capacity import ManagedWorkerPermit
from ._type_audit_admission import AuditIdentityReservation, AuditMaterializationResult
from ._type_plan_set_authority import PlanSetBindRequest, PlanSetBindResult
from ._type_skill_semantics import SemanticAdaptationContext

__all__ = [
    "GateState",
    "BackgroundSupervisor",
    "ManagedFixedBatchSupervisor",
    "ManagedWorkerCapacity",
    "ManagedJoinAttestationAuthority",
    "KitchenTransitionLock",
    "QuotaPolicy",
    "QuotaRefreshTask",
    "TokenFactory",
    "CampaignProtector",
    "AuditAuthorityMaterializer",
    "CommittedDispositionResolver",
    "PlanSetMaterializer",
]


class ManagedJoinAttestationAuthority(Protocol):
    """Server-owned issuer and verifier for managed-join adaptation evidence."""

    @property
    def activation_epoch(self) -> int: ...

    def issue(
        self,
        *,
        backend: str,
        launch_context: str,
        parent_session_id: str,
        direct_tool_mode: bool,
        resolved_model: str,
        resolved_reasoning_effort: str,
        codex_catalog_digest: str,
        fixed_batch_tool_registry_digest: str,
        hook_registry_digest: str,
        skill_load_applies: bool,
        guards_apply: bool,
    ) -> SemanticAdaptationContext: ...

    def verify(
        self,
        context: SemanticAdaptationContext | None,
        *,
        backend: str,
        parent_session_id: str,
    ) -> SemanticAdaptationContext | None: ...

    def find_verified_context(
        self,
        *,
        backend: str,
        parent_session_id: str,
    ) -> SemanticAdaptationContext | None: ...


@runtime_checkable
class AuditAuthorityMaterializer(Protocol):
    """Server-owned publisher for audit authority artifacts."""

    def materialize(
        self,
        *,
        reservation: AuditIdentityReservation,
        semantic_result_path: Path,
        preflight_step_names: tuple[str, ...],
    ) -> AuditMaterializationResult: ...


@runtime_checkable
class CommittedDispositionResolver(Protocol):
    """Lookup boundary for committed audit disposition artifacts."""

    def resolve(self, *, authority_digest: str, plan_digest: str) -> Path | None: ...


@runtime_checkable
class GateState(Protocol):
    """Protocol for gate enable/disable state."""

    @property
    def enabled(self) -> bool: ...

    def enable(self) -> None: ...

    def disable(self) -> None: ...


@runtime_checkable
class BackgroundSupervisor(Protocol):
    """Protocol for supervised background task execution."""

    @property
    def pending_count(self) -> int: ...

    def submit(
        self,
        coro: Any,
        *,
        on_exception: Any | None = None,
        status_path: Any | None = None,
        label: str = "",
    ) -> Any: ...

    async def drain(self) -> None: ...


@runtime_checkable
class ManagedFixedBatchSupervisor(Protocol):
    """Server-owned managed fixed-batch lifecycle and recovery authority."""

    @property
    def recovery_ready(self) -> bool: ...

    @property
    def recovery_diagnostic(self) -> str: ...

    async def reconcile_startup(self) -> bool: ...

    async def run(self, binding: Any) -> Any: ...

    def read_result(
        self,
        *,
        reference: str,
        launch: Any,
        batch_id: str,
        assignment_id: str = "",
    ) -> Any: ...

    async def close(self) -> None: ...


@runtime_checkable
class ManagedWorkerCapacity(Protocol):
    """Protocol for the process-wide owner-bound managed worker capacity."""

    def at_capacity(self) -> bool: ...

    async def acquire(self, owner: object) -> ManagedWorkerPermit: ...

    def release(self, permit: ManagedWorkerPermit) -> None: ...

    def reconfigure(self, *, max_concurrent: int, timeout: float | None) -> None: ...

    def restore_owner_debt(self, owner: object, permit_id: str) -> ManagedWorkerPermit: ...

    @property
    def active_count(self) -> int: ...

    @property
    def max_concurrent(self) -> int: ...

    @property
    def timeout(self) -> float | None: ...


@runtime_checkable
class KitchenTransitionLock(Protocol):
    """Synchronous lock protecting one ToolContext kitchen transition snapshot."""

    def __enter__(self) -> Any: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any | None,
    ) -> bool | None: ...


@runtime_checkable
class QuotaRefreshTask(Protocol):
    """Protocol for a cancellable background task handle.

    Satisfied by asyncio.Task — used to type the kitchen-scoped quota
    refresh task stored in ToolContext without leaking asyncio.Task into the
    core layer.
    """

    def cancel(self, msg: Any = None) -> bool: ...


@runtime_checkable
class TokenFactory(Protocol):
    """Protocol for resolving a GitHub token via the config → env → CLI fallback chain.

    Satisfied by any zero-argument callable that returns a token string or None.
    Set by make_context() on ToolContext; None in test ToolContext instances unless
    explicitly provided.
    """

    def __call__(self) -> str | None: ...


@runtime_checkable
class CampaignProtector(Protocol):
    """Protocol for resolving the set of protected campaign IDs for session retention.

    Satisfied by any callable that accepts a project root Path and returns a frozenset
    of campaign ID strings that should not be purged during log retention.
    Set by make_context() on ToolContext; None in test ToolContext instances unless
    explicitly provided.
    """

    def __call__(self, project_dir: Path) -> frozenset[str]: ...


@runtime_checkable
class PlanSetMaterializer(Protocol):
    """Server-owned binder for a content-addressed plan-set authority."""

    async def bind(self, request: PlanSetBindRequest) -> PlanSetBindResult: ...


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    """Typed quota capability derived from backend capabilities.

    supports_quota_check is True when the active backend is
    Anthropic-provider-capable; False when ctx.backend is None or
    non-Anthropic.
    """

    supports_quota_check: bool = False
