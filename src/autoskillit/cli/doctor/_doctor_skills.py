"""Bundled skill capability-contract diagnostics."""

from __future__ import annotations

from autoskillit.core import (
    Severity,
    SkillContractError,
    validate_skill_capability_roles,
)
from autoskillit.workspace import (
    DefaultSkillResolver,
    validate_skill_capability_authenticity,
)

from ._doctor_types import DoctorResult


def _check_skill_capability_authenticity(
    resolver: DefaultSkillResolver | None = None,
) -> list[DoctorResult]:
    """Report invalid bundled capability authenticity and exact-role contracts."""
    skill_resolver = resolver or DefaultSkillResolver()
    results: list[DoctorResult] = []
    for skill in skill_resolver.list_all():
        diagnostics = [skill.invalid_reason] if skill.invalid_reason is not None else []
        if skill.execution_role is None:
            diagnostics.append("execution role is missing or invalid")
        else:
            try:
                validate_skill_capability_roles(
                    skill.uses_capabilities,
                    skill.execution_role,
                )
            except SkillContractError as exc:
                diagnostics.append(str(exc))
        diagnostics.extend(validate_skill_capability_authenticity(skill))
        emitted: list[str] = []
        for diagnostic in diagnostics:
            if diagnostic is None or any(diagnostic in existing for existing in emitted):
                continue
            emitted.append(diagnostic)
        results.extend(
            DoctorResult(
                severity=Severity.ERROR,
                check="skill_capability_authenticity",
                message=f"{skill.path}: {diagnostic}",
            )
            for diagnostic in emitted
        )
    if results:
        return results
    return [
        DoctorResult(
            severity=Severity.OK,
            check="skill_capability_authenticity",
            message="Bundled skill capability declarations match source evidence.",
        )
    ]
