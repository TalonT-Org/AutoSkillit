"""Pure aggregate coverage evaluation over parsed allocation evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ..types._type_plan_set_authority import (
    AllocationKind,
    AllocationRowDef,
    AssignedRequirementDef,
    CoverageResultDef,
    CoverageStatus,
    InventoryMode,
    PlanSetAuthority,
    RequirementDef,
    RequirementKind,
)

__all__ = ["assigned_requirements", "evaluate_coverage"]


def evaluate_coverage(
    mode: InventoryMode,
    requirements: tuple[RequirementDef, ...],
    rows_by_part: Mapping[str, tuple[AllocationRowDef, ...]],
) -> CoverageResultDef:
    rows = tuple(row for part_rows in rows_by_part.values() for row in part_rows)
    if mode in {InventoryMode.UNENUMERATED, InventoryMode.NO_ISSUE}:
        empty = tuple(key for key, part_rows in rows_by_part.items() if not part_rows)
        return CoverageResultDef(
            CoverageStatus.PASS if not empty else CoverageStatus.FAIL,
            parts_without_obligations=empty,
        )

    by_id = {item.requirement_id: item for item in requirements}
    known = set(by_id)
    unknown = sorted(
        {
            row.requirement_id
            for row in rows
            if row.requirement_id not in known and not re.fullmatch(r"P-\d+", row.requirement_id)
        }
    )
    container_allocated = sorted(
        {
            row.requirement_id
            for row in rows
            if by_id.get(row.requirement_id, None)
            and by_id[row.requirement_id].kind is RequirementKind.CONTAINER
        }
    )
    allocations: dict[str, list[AllocationRowDef]] = {}
    for row in rows:
        allocations.setdefault(row.requirement_id, []).append(row)
    duplicate: set[str] = set()
    for requirement_id, entries in allocations.items():
        if len(entries) < 2:
            continue
        if not all(entry.allocation is AllocationKind.SHARED for entry in entries):
            duplicate.add(requirement_id)
        elif len({entry.part_key for entry in entries}) != len(entries):
            duplicate.add(requirement_id)
    unassigned = sorted(
        item.requirement_id
        for item in requirements
        if item.kind is not RequirementKind.CONTAINER and item.requirement_id not in allocations
    )
    uncovered = sorted(
        item.requirement_id
        for item in requirements
        if item.kind is RequirementKind.CONTAINER
        and any(
            child.parent_label == item.label and child.requirement_id in unassigned
            for child in requirements
        )
    )
    status = (
        CoverageStatus.PASS
        if not (unassigned or duplicate or unknown or container_allocated)
        else CoverageStatus.FAIL
    )
    return CoverageResultDef(
        status,
        unassigned=tuple(unassigned),
        uncovered=tuple(uncovered),
        duplicate=tuple(sorted(duplicate)),
        unknown=tuple(unknown),
        container_allocated=tuple(container_allocated),
    )


def assigned_requirements(
    authority: PlanSetAuthority,
    part_key: str,
) -> tuple[AssignedRequirementDef, ...]:
    requirements = {item.requirement_id: item for item in authority.requirements}
    result: list[AssignedRequirementDef] = []
    for row in authority.allocations:
        requirement = requirements.get(row.requirement_id)
        if row.part_key != part_key or requirement is None:
            continue
        result.append(
            AssignedRequirementDef(
                requirement_id=requirement.requirement_id,
                label=requirement.label,
                kind=requirement.kind,
                text=requirement.text,
                source_line=requirement.source_line,
                allocation=row.allocation,
                implementation_step=row.implementation_step,
            )
        )
    return tuple(result)
