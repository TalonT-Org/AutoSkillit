"""Small real-Git fixtures for checkout and linked-worktree tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 10


def _run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def init_empty_checkout(path: Path) -> Path:
    """Initialize a checkout with local identity but no commit."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git("init", cwd=path)
    _run_git("config", "user.name", "Test User", cwd=path)
    _run_git("config", "user.email", "test@example.com", cwd=path)
    return path


def init_checkout(path: Path) -> Path:
    """Initialize a checkout with local identity and one initial commit."""
    repo = init_empty_checkout(path)
    _run_git("commit", "--allow-empty", "-m", "initial commit", cwd=repo)
    return repo


def add_linked_worktree(main: Path, name: str, *, start: str | None = None) -> Path:
    """Add a linked worktree beside the checkout under ``worktrees/``."""
    path = main.parent / "worktrees" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-C", str(main), "worktree", "add", "-b", name, str(path)]
    if start is not None:
        args.append(start)
    _run_git(*args)
    return path


def commit_file(repo: Path, relpath: str, content: str, message: str) -> str:
    """Write, stage, and commit one file, returning the resulting HEAD SHA."""
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _run_git("add", "--", relpath, cwd=repo)
    _run_git("commit", "-m", message, cwd=repo)
    return head(repo)


def head(repo: Path) -> str:
    """Return the repository's current HEAD SHA."""
    return _run_git("rev-parse", "HEAD", cwd=repo).stdout.strip()
