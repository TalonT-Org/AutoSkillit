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


GENERATED_FILES: frozenset[str] = frozenset(
    {
        "src/autoskillit/hooks/hooks.json",
        ".claude/settings.json",
    }
)
