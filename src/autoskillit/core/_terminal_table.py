"""Shared terminal table primitive — color-agnostic, pure stdlib.

This module contains no autoskillit imports and no ANSI sequences. It lives
at L0 (core/) so it can be imported safely from any layer: cli/ (L3),
pipeline/ (L1), server/ (L3), etc.

Public surface: TerminalColumn, _render_terminal_table.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple


class TerminalColumn(NamedTuple):
    """Column specification for terminal table rendering.

    max_width caps the column content. Any cell value exceeding max_width is
    truncated and a single '…' character is appended. Setting max_width=None
    means unbounded (use only for the final/rightmost column that cannot wrap).
    """

    label: str
    max_width: int | None
    align: str  # "<" (left) or ">" (right)


def _render_terminal_table(
    columns: Sequence[TerminalColumn],
    rows: Sequence[Sequence[str]],
) -> str:
    """Render a terminal table from structured rows using TerminalColumn specs.

    Each column's width is the minimum of (max(len(cell) for cell in column), max_width).
    Cells exceeding max_width are truncated with '…'. Every output line is
    prefixed with two spaces for visual indentation.
    """
    # Compute capped column widths
    col_widths = []
    for i, col in enumerate(columns):
        data_width = max(
            (len(row[i]) for row in rows if i < len(row)),
            default=0,
        )
        data_width = max(data_width, len(col.label))
        if col.max_width is not None:
            data_width = min(data_width, col.max_width)
        col_widths.append(data_width)

    def _cell(value: str, width: int, align: str) -> str:
        if len(value) > width:
            value = value[: width - 1] + "…"
        return format(value, f"{align}{width}")

    lines: list[str] = []
    # Header row
    header_cells = [_cell(col.label, col_widths[i], col.align) for i, col in enumerate(columns)]
    lines.append("  " + "  ".join(header_cells))

    # Data rows
    for row in rows:
        cells = [
            _cell(row[i] if i < len(row) else "", col_widths[i], columns[i].align)
            for i in range(len(columns))
        ]
        lines.append("  " + "  ".join(cells))

    return "\n".join(lines)
