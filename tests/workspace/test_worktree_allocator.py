"""Real Git coverage for detached worktree allocation and removal."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.process import DefaultSubprocessRunner
from autoskillit.workspace import create_git_worktree, list_git_worktrees, remove_git_worktree

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


@pytest.mark.anyio
async def test_creates_and_removes_detached_worktree_with_real_runner(
    git_repo: Path, tmp_path: Path
) -> None:
    runner = DefaultSubprocessRunner()
    worktree_root = tmp_path / "worktrees"
    destination = worktree_root / "assignment"

    created = await create_git_worktree(git_repo, worktree_root, destination, "HEAD", runner)

    assert created == destination
    assert destination.is_dir()
    assert (destination / ".git").is_file()
    assert await list_git_worktrees(git_repo, worktree_root, runner) == [destination]

    removed = await remove_git_worktree(destination, git_repo, runner)

    assert removed.failed == []
    assert str(destination) in removed.deleted
    assert not destination.exists()
