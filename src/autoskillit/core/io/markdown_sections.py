"""Small fail-closed Markdown section and pipe-table primitives (IL-0)."""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = ["STEP_HEADING_RE", "extract_section", "parse_pipe_table", "split_table_row"]

STEP_HEADING_RE = re.compile(
    r"^###\s+(Step\s+[1-9][0-9]*(?:\.[1-9][0-9]*)*)(?::[^\n]*)?$",
    re.MULTILINE,
)


def extract_section(markdown: str, heading: str) -> str:
    """Return the body belonging to the level-2 Markdown heading."""
    pattern = re.compile(rf"^## {re.escape(heading)}[ \t]*$", re.MULTILINE)
    matches = tuple(pattern.finditer(markdown))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one ## {heading} section")
    start = matches[0].end()
    next_heading = re.search(r"^##\s+", markdown[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading is not None else len(markdown)
    return markdown[start:end]


def split_table_row(line: str) -> tuple[str, ...]:
    """Parse one strictly pipe-delimited row."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ValueError("table rows must be pipe-delimited")
    return tuple(cell.strip() for cell in stripped[1:-1].split("|"))


def parse_pipe_table(
    section: str,
    expected_header: Iterable[str],
) -> tuple[tuple[str, ...], ...]:
    """Parse a strict pipe table with a fixed header and Markdown separator."""
    lines = tuple(line for line in section.splitlines() if line.strip())
    header = tuple(expected_header)
    if len(lines) < 2:
        raise ValueError("table must contain a header and separator")
    if split_table_row(lines[0]) != header:
        raise ValueError(f"table header must be {header!r}")
    separator = split_table_row(lines[1])
    if len(separator) != len(header) or any(
        re.fullmatch(r":?-{3,}:?", cell) is None for cell in separator
    ):
        raise ValueError("table separator is invalid")
    rows = tuple(split_table_row(line) for line in lines[2:])
    if any(len(row) != len(header) for row in rows):
        raise ValueError(f"table rows must have exactly {len(header)} columns")
    return rows
