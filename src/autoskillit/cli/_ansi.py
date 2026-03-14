"""Terminal color utilities (L3 — CLI layer only)."""

from __future__ import annotations

import os
import sys


def supports_color() -> bool:
    """Return True if the terminal supports ANSI color output.

    Respects ``NO_COLOR`` (https://no-color.org/) and ``TERM=dumb``.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


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

    _skip = {"<!--", "```", "Agent-managed:"}
    out: list[str] = []
    in_table_section = False
    saw_title = False
    for ln in md.splitlines():
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
        # Skip the entire Inputs section (table is generated from recipe YAML)
        if ln.startswith("### Inputs"):
            in_table_section = True
            continue
        if in_table_section:
            if ln.startswith("### ") or (ln.strip() == "" and not ln.startswith("|")):
                # Hit next section or blank line after table — stop skipping
                if ln.startswith("### "):
                    continue  # skip the next section header too
                in_table_section = False
                # Don't skip this blank line — it's after the table
            else:
                continue  # skip table rows and separators
        if ln.startswith("### "):
            continue
        out.append(ln)

    # Collapse consecutive blank lines
    cleaned: list[str] = []
    for ln in out:
        if not ln.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(ln)
    return "\n".join(cleaned)


def ingredients_to_terminal(table: str) -> str:
    """Render a GFM pipe table as a colored terminal table.

    Takes the output of ``_format_ingredients_table`` (a GFM markdown table)
    and renders it with ANSI colors for terminal display.
    """
    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _D = "\x1b[2m" if color else ""
    _G = "\x1b[32m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _R = "\x1b[0m" if color else ""

    rows: list[list[str]] = []
    for ln in table.splitlines():
        if "---" in ln:
            continue  # separator
        if "|" not in ln:
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) >= 3:
            rows.append(cells)

    if not rows:
        return table

    nw = max(len(r[0]) for r in rows)
    dw = max(len(r[1]) for r in rows)
    header, *data = rows
    lines: list[str] = []
    lines.append(f"  {_B}{_D}{header[0]:>{nw}}  {header[1]:<{dw}}  {header[2]}{_R}")
    for r in data:
        lines.append(f"  {_G}{r[0]:>{nw}}{_R}  {r[1]:<{dw}}  {_Y}{r[2]}{_R}")
    return "\n".join(lines)
