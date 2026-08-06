"""Bundled and project-local skill capability-contract diagnostics."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import (
    SKILL_CONTRACT_REMEDIATIONS,
    RemediationAction,
    Severity,
    SkillContractError,
    validate_skill_capability_roles,
)
from autoskillit.workspace import (
    DefaultSkillResolver,
    render_skill_invalidities,
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
        diagnostics = [render_skill_invalidities(skill.invalidities)] if skill.invalidities else []
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
        diagnostics.extend(
            diagnostic.detail for diagnostic in validate_skill_capability_authenticity(skill)
        )
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


def _check_project_local_skill_contracts(
    resolver: DefaultSkillResolver | None = None,
    project_dir: Path | None = None,
) -> list[DoctorResult]:
    """Report invalid or shadowed project-local skill contracts.

    Covers the erosion the resolution-boundary containment step deliberately
    still surfaces: a stale/broken project-local copy (shadowing a bundled
    twin, or standing alone with no bundled twin) is excluded from the
    effective catalog rather than crashing composition, but that exclusion
    would otherwise carry a log line nobody reads. This is the check that
    makes it operator-visible.
    """
    skill_resolver = resolver or DefaultSkillResolver()
    root = project_dir or Path.cwd()
    _effective, exclusions = skill_resolver.scan_effective(root)
    if not exclusions:
        return [
            DoctorResult(
                severity=Severity.OK,
                check="project_local_skill_contracts",
                message="Project-local skill contracts validate cleanly.",
            )
        ]
    results: list[DoctorResult] = []
    for exclusion in exclusions:
        kinds = sorted({item.kind.value for item in exclusion.invalidities})
        message = f"{exclusion.path}: {', '.join(kinds)}"
        if exclusion.hints:
            message += "; hint: " + "; ".join(exclusion.hints)
        deterministic = any(
            SKILL_CONTRACT_REMEDIATIONS[item.kind].action is RemediationAction.DETERMINISTIC
            for item in exclusion.invalidities
        )
        if deterministic:
            message += "; run: autoskillit migrate --fix"
        results.append(
            DoctorResult(
                severity=Severity.WARNING,
                check="project_local_skill_contracts",
                message=message,
            )
        )
    return results
