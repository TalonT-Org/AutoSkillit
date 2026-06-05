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
import os
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import NamedTuple


def default_log_dir() -> Path:
    """Platform-default session diagnostics log directory (XDG-aware).

    Linux: $XDG_DATA_HOME/autoskillit/logs (fallback ~/.local/share/autoskillit/logs)
    macOS: ~/Library/Application Support/autoskillit/logs
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "autoskillit" / "logs"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "autoskillit" / "logs"


def pkg_root() -> Path:
    """Return the canonical autoskillit package root directory.

    Uses importlib.resources.files('autoskillit') — a named reference
    to the package root that does not depend on __file__ or parent-count
    assumptions about any specific module's depth within the package.

    Returns the same path as Path(__file__).parent when called from
    __init__.py, but is stable regardless of which sub-module calls it.
    """
    return Path(str(ir.files("autoskillit")))


def claude_code_project_dir(cwd: str) -> Path:
    """Derive the Claude Code project log directory from a working directory path.

    Encodes the cwd by replacing '/' and '_' with '-', matching Claude Code's
    internal convention for ~/.claude/projects/<encoded-path>/.
    """
    project_hash = cwd.replace("/", "-").replace("_", "-")
    return Path.home() / ".claude" / "projects" / project_hash


def claude_code_log_path(cwd: str, session_id: str) -> Path | None:
    """Compute the full path to a Claude Code conversation log file.

    Returns None when session_id is empty or is a fallback ID
    (no_session_* or crashed_*), since these don't correspond to
    real Claude Code conversation logs.
    """
    if not session_id or session_id.startswith("no_session_") or session_id.startswith("crashed_"):
        return None
    return claude_code_project_dir(cwd) / f"{session_id}.jsonl"


def find_latest_session_id(cwd: str | None = None) -> str | None:
    """Return the session_id of the most recent Claude Code session for cwd.

    Scans ~/.claude/projects/<encoded-cwd>/ for .jsonl files and returns
    the stem of the most recently modified one. Returns None when no
    sessions exist for the given directory.

    Parameters
    ----------
    cwd
        Working directory path string. Defaults to the current working directory.
    """
    effective_cwd = cwd if cwd is not None else str(Path.cwd())
    project_dir = claude_code_project_dir(effective_cwd)
    if not project_dir.exists():
        return None

    def _safe_mtime(f: Path) -> float:
        try:
            return f.stat().st_mtime
        except OSError:
            return 0.0

    jsonl_files = sorted(
        (f for f in project_dir.glob("*.jsonl")),
        key=_safe_mtime,
        reverse=True,
    )
    if not jsonl_files:
        return None
    return jsonl_files[0].stem


class _GitAncestorKind(Enum):
    MAIN = "main"
    WORKTREE = "worktree"


class _GitAncestorResult(NamedTuple):
    kind: _GitAncestorKind
    root: Path


def _find_git_ancestor(path: Path) -> _GitAncestorResult | None:
    """Walk the ancestor chain to find the nearest structurally valid git root.

    A .git *file* indicates a linked worktree.  A .git *directory* is only
    accepted when it contains a ``HEAD`` file (matching git's own
    ``setup.c:is_git_directory()``).  Empty/stale .git directories are
    walked past so a valid repo higher in the tree can still be found.
    """
    for parent in [path, *path.parents]:
        git_path = parent / ".git"
        if git_path.is_file():
            return _GitAncestorResult(_GitAncestorKind.WORKTREE, parent)
        if git_path.is_dir():
            if (git_path / "HEAD").is_file():
                return _GitAncestorResult(_GitAncestorKind.MAIN, parent)
            continue
    return None


def is_git_worktree(path: Path) -> bool:
    """Return True if path is inside a git linked worktree.

    A linked worktree has a .git FILE (not directory) somewhere in its
    ancestor chain. The main checkout has a .git DIRECTORY. Directories
    with no .git ancestor are not in a git repo at all (returns False).

    Uses only filesystem operations — no subprocess or git required.
    This is the fast, reliable heuristic for pre-install validation.
    """
    result = _find_git_ancestor(path)
    return result is not None and result.kind == _GitAncestorKind.WORKTREE


def is_git_main_checkout(path: Path) -> bool:
    """Return True if ``path`` is inside a git main checkout (has a .git directory).

    Returns False for worktrees (.git file) and for directories not inside any
    git repository.

    This is the semantic inverse of ``is_git_worktree()`` for the "main checkout"
    case — it differs in that "not in a git repo" returns False (not True).
    """
    result = _find_git_ancestor(path)
    return result is not None and result.kind == _GitAncestorKind.MAIN


def is_in_git_repo(path: Path) -> bool:
    """Return True if path is inside any git repository (worktree or main checkout).

    This is the union of is_git_worktree() and is_git_main_checkout().
    Use this when the caller needs "any git context" without caring about
    the worktree vs main checkout distinction.
    """
    return _find_git_ancestor(path) is not None


def resolve_main_worktree(path: Path) -> Path | None:
    """Resolve ``path`` to the main git worktree root.

    If ``path`` is inside a linked worktree, returns the main checkout root.
    If ``path`` is already the main checkout, returns that directory.
    If ``path`` is not inside any git repository, returns None.

    Uses ``git rev-parse --path-format=absolute --git-common-dir`` to locate
    the shared .git directory, then derives the main worktree root as its parent.
    This is the canonical resolution — works regardless of how deeply nested
    the path is within the worktree graph.
    """

    try:
        git_common_dir = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    output = git_common_dir.stdout.strip()
    if not output or not Path(output).is_absolute():
        return None
    return Path(output).parent.resolve()


GENERATED_FILES: frozenset[str] = frozenset(
    {
        "src/autoskillit/hooks/hooks.json",
        ".claude/settings.json",
        "src/autoskillit/recipes/contracts/",
    }
)


def is_generated_path(file_path: str) -> bool:
    """Return True if file_path matches any GENERATED_FILES entry.

    Expects ``file_path`` to be a repo-relative path (e.g. ``'src/autoskillit/hooks/hooks.json'``).
    Absolute paths will not match the repo-relative entries in ``GENERATED_FILES``.

    Handles both exact-path entries (e.g. 'src/autoskillit/hooks/hooks.json')
    and directory-prefix entries ending with '/' (e.g. 'src/autoskillit/recipes/diagrams/').
    """
    for entry in GENERATED_FILES:
        if entry.endswith("/"):
            if file_path.startswith(entry):
                return True
        elif file_path == entry:
            return True
    return False
