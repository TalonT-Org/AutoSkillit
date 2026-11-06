"""Small real-Git fixtures and environment isolation for checkout and linked-worktree tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

_GIT_TIMEOUT_SECONDS = 10

# Ambient variables that redirect Git or change its option parsing; Git-spawning tests clear them.
AMBIENT_GIT_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_TEST_DISALLOW_ABBREVIATED_OPTIONS",
)


def _run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def isolate_git_environment(
    mp: pytest.MonkeyPatch, home: Path, *, ceiling: Path | None = None
) -> None:
    """Point Git's user, system and discovery state into the test tree; drop ambient overrides."""
    home.mkdir(parents=True, exist_ok=True)
    mp.setenv("HOME", str(home))
    mp.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    mp.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    mp.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    mp.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
    if ceiling is not None:
        mp.setenv("GIT_CEILING_DIRECTORIES", str(ceiling))
    for name in AMBIENT_GIT_VARIABLES:
        mp.delenv(name, raising=False)


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
