"""Bundled skill capability-contract diagnostics."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.workspace import (
    DefaultSkillResolver,
    validate_skill_capability_authenticity,
)

from ._doctor_types import DoctorResult


def _check_skill_capability_authenticity(
    resolver: DefaultSkillResolver | None = None,
) -> list[DoctorResult]:
    """Report bundled capability declarations that disagree with source evidence."""
    skill_resolver = resolver or DefaultSkillResolver()
    results = [
        DoctorResult(
            severity=Severity.ERROR,
            check="skill_capability_authenticity",
            message=f"{skill.path}: {diagnostic}",
        )
        for skill in skill_resolver.list_all()
        for diagnostic in validate_skill_capability_authenticity(skill)
    ]
    if results:
        return results
    return [
        DoctorResult(
            severity=Severity.OK,
            check="skill_capability_authenticity",
            message="Bundled skill capability declarations match source evidence.",
        )
    ]
