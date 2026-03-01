"""Canonical package root path resolution.

All code that needs the autoskillit package root directory (e.g., to locate
bundled skills, recipes, migrations, or plugin.json) must use pkg_root()
from this module. Direct __file__-based path resolution is forbidden elsewhere.

Design rationale:
- Uses importlib.resources.files() for a named, depth-independent reference
- Single point of truth: change once, fixes all callers
- Testable in isolation (mock importlib.resources.files in tests)
- Robust to module reorganization (no parent-count assumptions)
"""

from __future__ import annotations

import importlib.resources as ir
from pathlib import Path


def pkg_root() -> Path:
    """Return the canonical autoskillit package root directory.

    Uses importlib.resources.files('autoskillit') — a named reference
    to the package root that does not depend on __file__ or parent-count
    assumptions about any specific module's depth within the package.

    Returns the same path as Path(__file__).parent when called from
    __init__.py, but is stable regardless of which sub-module calls it.
    """
    return Path(str(ir.files("autoskillit")))


def is_git_worktree(path: Path) -> bool:
    """Return True if path is inside a git linked worktree.

    A linked worktree has a .git FILE (not directory) somewhere in its
    ancestor chain. The main checkout has a .git DIRECTORY. Directories
    with no .git ancestor are not in a git repo at all (returns False).

    Uses only filesystem operations — no subprocess or git required.
    This is the fast, reliable heuristic for pre-install validation.
    """
    for parent in [path, *path.parents]:
        git_path = parent / ".git"
        if git_path.is_file():
            return True  # .git file = linked worktree
        if git_path.is_dir():
            return False  # .git dir = main checkout
    return False  # not in a git repo
