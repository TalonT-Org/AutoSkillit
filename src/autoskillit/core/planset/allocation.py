"""Strict allocation-table parsing and evidence validation."""

from __future__ import annotations

import re

from ..io.markdown_sections import STEP_HEADING_RE, extract_section, parse_pipe_table
from ..types.audit._type_plan_set_authority import (
    AllocationKind,
    AllocationRowDef,
    PlanSetRejectReason,
)

__all__ = ["parse_part_allocation", "verify_allocation_evidence"]

_HEADER = ("Requirement ID", "Allocation", "Implementation Step")


def parse_part_allocation(part_markdown: str, *, part_key: str) -> tuple[AllocationRowDef, ...]:
    section = extract_section(part_markdown, "Issue Requirement Allocation")
    rows = parse_pipe_table(section, _HEADER)
    result: list[AllocationRowDef] = []
    seen: set[str] = set()
    raw_rows = tuple(line for line in section.splitlines() if line.strip())[2:]
    for (requirement_id, allocation, implementation_step), raw_row in zip(
        rows, raw_rows, strict=True
    ):
        if not requirement_id:
            raise ValueError(
                f'Requirement ID is empty in row "{raw_row.strip()[:120]}"; '
                "use a non-empty bare Requirement ID"
            )
        if requirement_id in seen:
            raise ValueError(f"duplicate allocated requirement {requirement_id}")
        seen.add(requirement_id)
        if not implementation_step:
            raise ValueError(
                f"{requirement_id}: allocation implementation step is empty; "
                "use one matching heading key such as Step 2.1"
            )
        try:
            kind = AllocationKind(allocation.lower())
        except ValueError as exc:
            raise ValueError(
                f'{requirement_id}: unknown allocation "{allocation}"; use owned or shared'
            ) from exc
        result.append(
            AllocationRowDef(
                requirement_id=requirement_id,
                part_key=part_key,
                allocation=kind,
                implementation_step=implementation_step,
            )
        )
    return tuple(result)


def verify_allocation_evidence(
    part_markdown: str,
    rows: tuple[AllocationRowDef, ...],
) -> tuple[tuple[str, PlanSetRejectReason, str], ...]:
    """Ensure an allocation names a real step whose own text names the requirement."""
    matches = tuple(STEP_HEADING_RE.finditer(part_markdown))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        next_step = matches[index + 1].start() if index + 1 < len(matches) else len(part_markdown)
        next_section = re.search(r"^#{1,2}\s+", part_markdown[match.end() :], re.MULTILINE)
        section_end = (
            match.end() + next_section.start() if next_section is not None else len(part_markdown)
        )
        end = min(next_step, section_end)
        blocks[match.group(1)] = part_markdown[match.start() : end]
    errors: list[tuple[str, PlanSetRejectReason, str]] = []
    for row in rows:
        block = blocks.get(row.implementation_step)
        if block is None:
            found = (
                f"step keys found: {', '.join(blocks)}" if blocks else "no step heading was found"
            )
            errors.append(
                (
                    row.requirement_id,
                    PlanSetRejectReason.ALLOCATION_STEP_MISSING,
                    f'{row.requirement_id}: Implementation Step cell "{row.implementation_step}" '
                    f"does not match a step; {found}. Expected exactly one key such as Step 2.1, "
                    "copied from a ### Step N: title heading (### Step N.M: title for sub-steps).",
                )
            )
        elif row.requirement_id not in block:
            errors.append(
                (
                    row.requirement_id,
                    PlanSetRejectReason.ALLOCATION_STEP_UNREFERENCED,
                    f'Requirement ID cell "{row.requirement_id}" is not referenced in '
                    f"{row.implementation_step}; the bare ID must appear in that step's own "
                    "text, on the line under its heading.",
                )
            )
    return tuple(errors)
