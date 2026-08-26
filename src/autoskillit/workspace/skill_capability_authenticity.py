"""Skill capability authenticity validation entry points.

Owns ``SkillCapabilityValidation``, ``SkillCapabilityAuthenticityDiagnostic``,
``validate_skill_capability_authenticity``, ``validate_skill_capability_declarations``,
``detect_skill_capabilities``. Compares declaration-vs-evidence mismatches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

import autoskillit.workspace.skill_capabilities as _capabilities_facade
from autoskillit.core import SkillContractError, SkillInvalidityKind
from autoskillit.workspace.skill_capability_scanner import SkillCapabilityEvidence

if TYPE_CHECKING:
    from autoskillit.workspace.skills import SkillInfo


_AuthenticityDiagnostics = tuple["SkillCapabilityAuthenticityDiagnostic", ...]


@dataclass(frozen=True, slots=True)
class SkillCapabilityValidation:
    """Bidirectional comparison of declarations and genuine semantic evidence."""

    declared: frozenset[str]
    detected: frozenset[str]
    evidence: tuple[SkillCapabilityEvidence, ...]
    missing: frozenset[str]
    unsupported: frozenset[str]

    @property
    def valid(self) -> bool:
        return not self.missing and not self.unsupported


class SkillCapabilityAuthenticityDiagnostic(NamedTuple):
    kind: SkillInvalidityKind
    capability: str
    detail: str


def detect_skill_capabilities(
    content: str,
    skill_name: str | None = None,
) -> frozenset[str]:
    """Return capabilities backed by genuine self-outbound executable evidence."""
    return frozenset(
        evidence.capability
        for evidence in _capabilities_facade.classify_skill_capability_evidence(
            content, skill_name
        )
        if evidence.is_genuine
    )


def validate_skill_capability_declarations(
    body: str,
    skill_name: str,
    declared_capabilities: frozenset[str] | set[str] | tuple[str, ...] | list[str],
) -> SkillCapabilityValidation:
    """Compare declared capabilities with genuine evidence in both directions."""
    declared = frozenset(declared_capabilities)
    evidence = _capabilities_facade.classify_skill_capability_evidence(body, skill_name)
    detected = frozenset(item.capability for item in evidence if item.is_genuine)
    return SkillCapabilityValidation(
        declared=declared,
        detected=detected,
        evidence=evidence,
        missing=detected - declared,
        unsupported=declared - detected,
    )


def validate_skill_capability_authenticity(skill_info: SkillInfo) -> _AuthenticityDiagnostics:
    """Return stable diagnostics for declaration/evidence mismatches."""
    validation = validate_skill_capability_declarations(
        skill_info.canonical_content,
        skill_info.name,
        skill_info.uses_capabilities,
    )
    diagnostics: list[SkillCapabilityAuthenticityDiagnostic] = []
    for capability in sorted(validation.missing):
        genuine = next(
            (
                item
                for item in validation.evidence
                if item.capability == capability and item.is_genuine
            ),
            None,
        )
        if genuine is None:
            raise SkillContractError(
                f"{skill_info.name}: capability {capability!r} reported missing but no genuine "
                "evidence matches in classify_skill_capability_evidence output"
            )
        detail = (
            f"{skill_info.name}: missing declaration for {capability!r}; "
            f"lines {genuine.source_span[0]}-{genuine.source_span[1]}: "
            f"{genuine.source.strip()!r}"
        )
        diagnostics.append(
            SkillCapabilityAuthenticityDiagnostic(
                SkillInvalidityKind.UNDECLARED_CAPABILITY, capability, detail
            )
        )
    for capability in sorted(validation.unsupported):
        artifact = next(
            (item for item in validation.evidence if item.capability == capability),
            None,
        )
        evidence_detail = (
            f"only artifact evidence at lines "
            f"{artifact.source_span[0]}-{artifact.source_span[1]}: "
            f"{artifact.source.strip()!r}"
            if artifact is not None
            else "no source span: no recognizable evidence"
        )
        detail = (
            f"{skill_info.name}: declaration {capability!r} lacks genuine evidence; "
            f"{evidence_detail}"
        )
        diagnostics.append(
            SkillCapabilityAuthenticityDiagnostic(
                SkillInvalidityKind.UNKNOWN_CAPABILITY, capability, detail
            )
        )
    return tuple(diagnostics)


__all__ = [
    "SkillCapabilityAuthenticityDiagnostic",
    "SkillCapabilityValidation",
    "detect_skill_capabilities",
    "validate_skill_capability_authenticity",
    "validate_skill_capability_declarations",
]
