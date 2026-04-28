"""Terminal color utilities (L3 — CLI layer only)."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from autoskillit.core import TerminalColumn


def supports_color() -> bool:
    """Return True if the terminal supports ANSI color output.

    Respects ``NO_COLOR`` (https://no-color.org/) and ``TERM=dumb``.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _render_terminal_table(
    columns: Sequence[TerminalColumn],
    rows: Sequence[Sequence[str]],
) -> str:
    """Render a terminal table from structured rows using TerminalColumn specs.

    Each column's width is the minimum of (max(len(cell) for cell in column), max_width).
    Cells exceeding max_width are truncated with '…'.
    """
    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _D = "\x1b[2m" if color else ""
    _G = "\x1b[32m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _R = "\x1b[0m" if color else ""

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
    lines.append(f"  {_B}{_D}" + "  ".join(header_cells) + _R)

    # Data rows (with color)
    for row in rows:
        cells = [
            _cell(row[i] if i < len(row) else "", col_widths[i], columns[i].align)
            for i in range(len(columns))
        ]
        # Apply color: green for name col, yellow for last col
        cells[0] = f"{_G}{cells[0]}{_R}"
        if len(cells) >= 3:
            cells[-1] = f"{_Y}{cells[-1]}{_R}"
        lines.append("  " + "  ".join(cells))

    return "\n".join(lines)


_INGREDIENT_COLUMNS = (
    TerminalColumn("Name", max_width=30, align=">"),
    TerminalColumn("Description", max_width=60, align="<"),
    TerminalColumn("Default", max_width=20, align=">"),
)


def ingredients_to_terminal(
    rows: Sequence[tuple[str, str, str]],
) -> str:
    """Render recipe ingredients as an ANSI-colored terminal table.

    Args:
        rows: Sequence of (name, description, default) tuples as produced
              by ``recipe.build_ingredient_rows``.

    Returns:
        Multi-line string ready for print(). Description column is capped at
        60 characters; overflow is truncated with '…'.
    """
    return _render_terminal_table(_INGREDIENT_COLUMNS, rows)


def permissions_warning() -> str:
    """Return the --dangerously-skip-permissions disclaimer text."""
    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _W = "\x1b[33;1m" if color else ""
    _D = "\x1b[2m" if color else ""
    _R = "\x1b[0m" if color else ""
    return (
        f"{_W}WARNING:{_R} This session runs with {_B}--dangerously-skip-permissions{_R}.\n"
        f"{_D}All tool calls are auto-approved without prompting.{_R}"
    )


def diagram_to_terminal(md: str) -> str:
    """Convert a markdown diagram file to clean terminal output.

    Renders only the flow diagram. The inputs table is generated separately
    from the recipe YAML (single source of truth) by ``ingredients_to_terminal``.
    """
    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _C = "\x1b[96m" if color else ""
    _R = "\x1b[0m" if color else ""

    _skip = {"<!--", "Agent-managed:"}
    out: list[str] = []
    in_table_section = False
    in_fenced_block = False
    saw_title = False
    for ln in md.splitlines():
        if ln.startswith("```"):
            in_fenced_block = not in_fenced_block
            continue
        if in_fenced_block:
            continue
        if any(ln.startswith(s) for s in _skip):
            continue
        if ln.startswith("## "):
            out.append(f"{_B}{_C}{ln[3:].upper()} RECIPE{_R}")
            saw_title = True
            continue
        if saw_title:
            saw_title = False
            if ln.strip():
                continue
        if ln.startswith("### Inputs"):
            in_table_section = True
            continue
        if in_table_section:
            if ln.startswith("### "):
                in_table_section = False
                continue
            if ln.strip() == "" and not ln.startswith("|"):
                in_table_section = False
            else:
                continue
        if ln.startswith("### "):
            continue
        out.append(ln)

    cleaned: list[str] = []
    for ln in out:
        if not ln.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(ln)
    return "\n".join(cleaned)
