"""Shared stdlib-only utilities for hook scripts.

Imported by both PreToolUse guards (in guards/) and PostToolUse hooks
via sys.path bootstrap.  Stdlib-only — no autoskillit imports.
"""

from __future__ import annotations

import re
from pathlib import Path

STEP_SUFFIX_RE = re.compile(r"-\d+$")


def find_project_root() -> Path:
    """Walk up from CWD to find nearest ancestor containing .autoskillit/."""
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        if (ancestor / ".autoskillit").is_dir():
            return ancestor
    return cwd
