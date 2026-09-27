"""Audit artifact field-ownership definitions and registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from ._type_audit_admission_validation import _require_nonempty


class AuditArtifactFieldOwnership(StrEnum):
    """The sole authority allowed to supply one artifact field."""

    CHILD_SEMANTIC = "CHILD_SEMANTIC"
    SERVER_INJECTED = "SERVER_INJECTED"
    SERVER_DERIVED = "SERVER_DERIVED"
    VERIFIED_COPY = "VERIFIED_COPY"


@dataclass(frozen=True, slots=True)
class AuditArtifactFieldOwnershipDef:
    artifact_kind: str
    field_name: str
    ownership: AuditArtifactFieldOwnership

    def __post_init__(self) -> None:
        _require_nonempty("AuditArtifactFieldOwnershipDef.artifact_kind", self.artifact_kind)
        _require_nonempty("AuditArtifactFieldOwnershipDef.field_name", self.field_name)
        if not isinstance(self.ownership, AuditArtifactFieldOwnership):
            raise ValueError(
                "AuditArtifactFieldOwnershipDef.ownership must be an AuditArtifactFieldOwnership"
            )


def _ownership_registry() -> Mapping[tuple[str, str], AuditArtifactFieldOwnershipDef]:
    definitions: list[AuditArtifactFieldOwnershipDef] = []
    groups: dict[
        str,
        dict[AuditArtifactFieldOwnership, tuple[str, ...]],
    ] = {
        "semantic_result": {
            AuditArtifactFieldOwnership.CHILD_SEMANTIC: (
                "audited_plan_refs",
                "assessments",
                "verdict",
                "remediation_ref",
            ),
            AuditArtifactFieldOwnership.SERVER_DERIVED: ("schema_version",),
        },
        "standalone_evidence": {
            AuditArtifactFieldOwnership.CHILD_SEMANTIC: (
                "audited_plan_refs",
                "assessments",
                "verdict",
                "remediation_ref",
            ),
            AuditArtifactFieldOwnership.SERVER_DERIVED: ("schema_version", "kind"),
        },
        "authority": {
            AuditArtifactFieldOwnership.CHILD_SEMANTIC: (
                "assessments",
                "verdict",
                "remediation_ref",
            ),
            AuditArtifactFieldOwnership.SERVER_INJECTED: (
                "execution_generation",
                "cycle_id",
                "scope_id",
                "part_id",
                "audit_round",
                "generated_at",
            ),
            AuditArtifactFieldOwnership.SERVER_DERIVED: (
                "schema_version",
                "plan_set_id",
                "inventory_ref",
                "findings_digest",
                "authority_digest",
            ),
            AuditArtifactFieldOwnership.VERIFIED_COPY: (
                "parent_authority_digest",
                "audited_plan_refs",
            ),
        },
        "disposition_report": {
            AuditArtifactFieldOwnership.CHILD_SEMANTIC: ("dispositions",),
            AuditArtifactFieldOwnership.SERVER_INJECTED: ("generated_at",),
            AuditArtifactFieldOwnership.SERVER_DERIVED: (
                "schema_version",
                "current_plan_ref",
                "report_digest",
            ),
            AuditArtifactFieldOwnership.VERIFIED_COPY: (
                "execution_generation",
                "cycle_id",
                "plan_set_id",
                "scope_id",
                "part_id",
                "audit_round",
                "parent_authority_digest",
                "inventory_digest",
                "findings_digest",
            ),
        },
        "plan_association": {
            AuditArtifactFieldOwnership.SERVER_DERIVED: (
                "schema_version",
                "plan_ref",
                "disposition_ref",
                "association_digest",
            ),
            AuditArtifactFieldOwnership.VERIFIED_COPY: ("parent_authority_digest",),
        },
    }
    for artifact_kind, ownership_groups in groups.items():
        for ownership, field_names in ownership_groups.items():
            definitions.extend(
                AuditArtifactFieldOwnershipDef(
                    artifact_kind=artifact_kind,
                    field_name=field_name,
                    ownership=ownership,
                )
                for field_name in field_names
            )
    return MappingProxyType(
        {
            (definition.artifact_kind, definition.field_name): definition
            for definition in definitions
        }
    )


AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY = _ownership_registry()
