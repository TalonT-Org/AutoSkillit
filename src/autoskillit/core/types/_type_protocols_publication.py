"""Server-owned publication protocol definitions (audit authority and plan-set)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ._type_audit_admission import AuditIdentityReservation, AuditMaterializationResult
from ._type_plan_set_authority import PlanSetBindRequest, PlanSetBindResult

__all__ = [
    "AuditAuthorityMaterializer",
    "CommittedDispositionResolver",
    "PlanSetMaterializer",
]


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
class PlanSetMaterializer(Protocol):
    """Server-owned binder for a content-addressed plan-set authority."""

    async def bind(self, request: PlanSetBindRequest) -> PlanSetBindResult: ...
