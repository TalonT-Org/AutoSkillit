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
