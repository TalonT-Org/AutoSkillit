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
    """Convert a markdown diagram file to clean terminal output."""
    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _D = "\x1b[2m" if color else ""
    _G = "\x1b[32m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _C = "\x1b[96m" if color else ""
    _R = "\x1b[0m" if color else ""

    _skip = {"<!--", "```", "Agent-managed:"}
    out: list[str] = []
    table_rows: list[list[str]] = []
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
        if ln.startswith("### "):
            continue
        if "|--" in ln and set(ln.replace(" ", "")) <= {"|", "-"}:
            continue
        elif ln.startswith("| "):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if len(cells) >= 3:
                table_rows.append(cells)
            else:
                out.append("  ".join(cells))
        else:
            out.append(ln)

    # Render table with dynamic column width
    if table_rows:
        nw = max(len(r[0]) for r in table_rows)
        header, *rows = table_rows
        out.append(f"  {_B}{_D}{header[0]:>{nw}}  {header[1]:<36}  {header[2]}{_R}")
        for r in rows:
            out.append(f"  {_G}{r[0]:>{nw}}{_R}  {r[1]:<36}  {_Y}{r[2]}{_R}")

    # Collapse consecutive blank lines
    cleaned: list[str] = []
    for ln in out:
        if not ln.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(ln)
    return "\n".join(cleaned)
