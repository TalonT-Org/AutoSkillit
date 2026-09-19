"""Strict parsing of an audit plan's requirements and implementation steps."""

from __future__ import annotations

import re

from ..types._type_audit_cycle_disposition import PlanDispositionRow

_REQUIREMENTS_HEADER = ("Requirement ID", "Disposition", "Implementation Step")
_STEP_HEADING_RE = re.compile(
    r"^###\s+(Step\s+[1-9][0-9]*(?:\.[1-9][0-9]*)*)(?::[^\n]*)?$",
    re.MULTILINE,
)


def _extract_section(markdown: str, heading: str) -> str:
    pattern = re.compile(rf"^## {re.escape(heading)}[ \t]*$", re.MULTILINE)
    matches = tuple(pattern.finditer(markdown))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one ## {heading} section")
    start = matches[0].end()
    next_heading = re.search(r"^##\s+", markdown[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading is not None else len(markdown)
    return markdown[start:end]


def _split_table_row(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ValueError("Requirements Map rows must be pipe-delimited")
    return tuple(cell.strip() for cell in stripped[1:-1].split("|"))


def parse_requirements_map(markdown: str) -> tuple[PlanDispositionRow, ...]:
    """Parse the plan's exact Requirements Map table."""
    section = _extract_section(markdown, "Requirements Map")
    lines = tuple(line for line in section.splitlines() if line.strip())
    if len(lines) < 3:
        raise ValueError("Requirements Map must contain a header, separator, and rows")
    if _split_table_row(lines[0]) != _REQUIREMENTS_HEADER:
        raise ValueError(
            "Requirements Map header must be "
            "| Requirement ID | Disposition | Implementation Step |"
        )
    separator = _split_table_row(lines[1])
    if len(separator) != 3 or any(re.fullmatch(r":?-{3,}:?", cell) is None for cell in separator):
        raise ValueError("Requirements Map separator is invalid")
    rows: list[PlanDispositionRow] = []
    for line in lines[2:]:
        cells = _split_table_row(line)
        if len(cells) != 3:
            raise ValueError("Requirements Map rows must have exactly three columns")
        requirement_id, disposition, implementation_step = cells
        step = None if implementation_step in {"", "-", "—"} else implementation_step
        rows.append(
            PlanDispositionRow.create(
                requirement_id=requirement_id,
                disposition=disposition,
                implementation_step=step,
            )
        )
    ids = tuple(row.requirement_id for row in rows)
    if len(ids) != len(set(ids)):
        raise ValueError("Requirements Map contains duplicate requirement IDs")
    return tuple(rows)


def implementation_step_blocks(markdown: str) -> dict[str, str]:
    """Return each numbered Implementation Steps block by its canonical name."""
    section = _extract_section(markdown, "Implementation Steps")
    matches = tuple(_STEP_HEADING_RE.finditer(section))
    if not matches:
        raise ValueError("Implementation Steps must contain ### Step N directives")
    blocks: dict[str, str] = {}
    for index, matched in enumerate(matches):
        step_name = matched.group(1)
        if step_name in blocks:
            raise ValueError(f"duplicate implementation step {step_name}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        blocks[step_name] = section[matched.start() : end]
    return blocks
