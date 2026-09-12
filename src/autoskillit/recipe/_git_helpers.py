"""Backward-compat shim for ``recipe/_git_helpers.py``.

Real implementation: ``autoskillit.recipe.helpers._git_helpers`` (#4671 D).
Preserves old import path ``autoskillit.recipe._git_helpers``.
"""

from __future__ import annotations

from autoskillit.recipe.helpers._git_helpers import (
    _GIT_REMOTE_COMMAND_RE,
    _LITERAL_ORIGIN_RE,
    annotations,
    cmd_keyword_pattern,
)

__all__ = ["_GIT_REMOTE_COMMAND_RE", "_LITERAL_ORIGIN_RE", "annotations", "cmd_keyword_pattern"]
