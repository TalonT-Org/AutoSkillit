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
    in_table = False
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
                continue  # skip description line after title
        if ln.startswith("### "):
            continue
        if "|--" in ln and set(ln.replace(" ", "")) <= {"|", "-"}:
            continue
        elif ln.startswith("| "):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if len(cells) >= 3:
                if not in_table:
                    # header row
                    out.append(f"  {_B}{_D}{cells[0]:>18}  {cells[1]:<36}  {cells[2]}{_R}")
                    in_table = True
                else:
                    out.append(f"  {_G}{cells[0]:>18}{_R}  {cells[1]:<36}  {_Y}{cells[2]}{_R}")
            else:
                out.append("  ".join(cells))
        else:
            in_table = False
            out.append(ln)
    # Collapse consecutive blank lines into one
    cleaned: list[str] = []
    for ln in out:
        if not ln.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(ln)
    return "\n".join(cleaned)
