# tests/workspace/test_worktree.py

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import CleanupResult
from autoskillit.workspace.worktree import (
    list_git_worktrees,
    remove_git_worktree,
    remove_worktree_sidecar,
)


class TestListGitWorktrees:
    """list_git_worktrees(project_root) returns paths of linked worktrees under root."""

    @pytest.mark.anyio
    async def test_returns_linked_worktrees_under_prefix(self, tmp_path):
        """Only worktrees whose paths start with the given prefix are returned."""
        wt_root = tmp_path / "worktrees"
        wt_root.mkdir()
        (wt_root / "impl-foo-20260101-120000").mkdir()
        porcelain = (
            f"worktree {tmp_path}\nHEAD abc\nbranch refs/heads/main\n\n"
            f"worktree {wt_root}/impl-foo-20260101-120000\n"
            "HEAD def\nbranch refs/heads/impl-foo-20260101-120000\n\n"
        )
        runner = AsyncMock(return_value=MagicMock(returncode=0, stdout=porcelain, stderr=""))
        result = await list_git_worktrees(tmp_path, wt_root, runner)
        assert result == [wt_root / "impl-foo-20260101-120000"]

    @pytest.mark.anyio
    async def test_git_failure_returns_empty_list(self, tmp_path):
        """When git worktree list fails, returns empty list without raising."""
        runner = AsyncMock(return_value=MagicMock(returncode=1, stdout="", stderr="fatal"))
        result = await list_git_worktrees(tmp_path, tmp_path / "worktrees", runner)
        assert result == []

    @pytest.mark.anyio
    async def test_excludes_main_worktree(self, tmp_path):
        """Main worktree entry (first in porcelain output) is never in the result."""
        wt_root = tmp_path / "worktrees"
        porcelain = f"worktree {tmp_path}\nHEAD abc\nbranch refs/heads/main\n\n"
        runner = AsyncMock(return_value=MagicMock(returncode=0, stdout=porcelain, stderr=""))
        result = await list_git_worktrees(tmp_path, wt_root, runner)
        assert result == []


class TestRemoveGitWorktree:
    """remove_git_worktree(worktree_path, main_repo, runner) removes the worktree."""

    @pytest.mark.anyio
    async def test_success_returns_cleanup_result_deleted(self, tmp_path):
        wt = tmp_path / "worktrees" / "impl-foo"
        wt.mkdir(parents=True)
        runner = AsyncMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        result = await remove_git_worktree(wt, tmp_path, runner)
        assert isinstance(result, CleanupResult)
        assert str(wt) in result.deleted
        assert result.failed == []

    @pytest.mark.anyio
    async def test_git_failure_falls_back_to_shutil_rmtree(self, tmp_path):
        """When git worktree remove fails, falls back to shutil.rmtree for orphaned dirs."""
        wt = tmp_path / "worktrees" / "impl-orphan"
        wt.mkdir(parents=True)
        runner = AsyncMock(
            return_value=MagicMock(returncode=1, stdout="", stderr="not registered")
        )
        result = await remove_git_worktree(wt, tmp_path, runner)
        assert isinstance(result, CleanupResult)
        assert str(wt) in result.deleted
        assert not wt.exists()

    @pytest.mark.anyio
    async def test_both_git_and_rmtree_fail_records_failure(self, tmp_path):
        """When both git and shutil fail, failure is recorded, no exception raised."""
        wt = tmp_path / "worktrees" / "impl-locked"
        wt.mkdir(parents=True)
        runner = AsyncMock(return_value=MagicMock(returncode=1, stdout="", stderr="error"))
        with patch(
            "autoskillit.workspace.worktree.shutil.rmtree",
            side_effect=OSError("permission denied"),
        ):
            result = await remove_git_worktree(wt, tmp_path, runner)
        assert str(wt) in [p for p, _ in result.failed]

    @pytest.mark.anyio
    async def test_nonexistent_path_is_skipped(self, tmp_path):
        """A path that does not exist is added to skipped, not failed."""
        wt = tmp_path / "worktrees" / "impl-gone"
        runner = AsyncMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        result = await remove_git_worktree(wt, tmp_path, runner)
        assert str(wt) in result.skipped


class TestRemoveWorktreeSidecar:
    """remove_worktree_sidecar removes .autoskillit/temp/worktrees/<name>/."""

    def test_removes_sidecar_directory(self, tmp_path):
        name = "impl-foo-20260101-120000"
        sidecar = tmp_path / ".autoskillit" / "temp" / "worktrees" / name
        sidecar.mkdir(parents=True)
        (sidecar / "base-branch").write_text("main")
        result = remove_worktree_sidecar(tmp_path, name)
        assert isinstance(result, CleanupResult)
        assert not sidecar.exists()
        assert str(sidecar) in result.deleted

    def test_missing_sidecar_is_skipped_not_failed(self, tmp_path):
        result = remove_worktree_sidecar(tmp_path, "impl-absent")
        assert result.failed == []
        assert len(result.skipped) == 1
