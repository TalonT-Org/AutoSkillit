"""Narrow IL-0 service protocols for server-owned audit publication."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ._type_audit_admission import (
    AuditIdentityReservation,
    AuditMaterializationResult,
)

__all__ = ["AuditAuthorityMaterializer", "CommittedDispositionResolver"]


@runtime_checkable
class AuditAuthorityMaterializer(Protocol):
    def materialize(
        self,
        *,
        reservation: AuditIdentityReservation,
        semantic_result_path: Path,
        preflight_step_names: tuple[str, ...],
    ) -> AuditMaterializationResult: ...


@runtime_checkable
class CommittedDispositionResolver(Protocol):
    def resolve(
        self,
        *,
        authority_digest: str,
        plan_digest: str,
    ) -> Path | None: ...
