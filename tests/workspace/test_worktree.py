# tests/workspace/test_worktree.py

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import CleanupResult
from autoskillit.workspace import (
    list_git_worktrees,
    remove_git_worktree,
    remove_worktree_sidecar,
    write_worktree_sidecar,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


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
        assert any("impl-absent" in s for s in result.skipped), (
            f"Expected impl-absent path in skipped list; got: {result.skipped}"
        )


class TestWriteWorktreeSidecar:
    """write_worktree_sidecar creates .autoskillit/temp/worktrees/<name>/base-branch."""

    def test_writes_base_branch_file(self, tmp_path) -> None:
        """File is created at the correct path with correct content."""
        path = write_worktree_sidecar(tmp_path, "impl-foo-20260101-120000", "develop")
        assert path.exists()
        assert path.read_text().strip() == "develop"

    def test_creates_intermediate_directories(self, tmp_path) -> None:
        """Intermediate directories are created if they don't exist."""
        write_worktree_sidecar(tmp_path, "impl-bar-20260201-130000", "main")
        expected = tmp_path / ".autoskillit" / "temp" / "worktrees" / "impl-bar-20260201-130000"
        assert (expected / "base-branch").exists()

    def test_overwrites_existing_sidecar(self, tmp_path) -> None:
        """Writing again replaces the existing content."""
        name = "impl-baz-20260301-140000"
        write_worktree_sidecar(tmp_path, name, "main")
        write_worktree_sidecar(tmp_path, name, "develop")
        path = tmp_path / ".autoskillit" / "temp" / "worktrees" / name / "base-branch"
        assert path.read_text().strip() == "develop"

    def test_returns_written_path(self, tmp_path) -> None:
        """Return value is the path to the sidecar file."""
        path = write_worktree_sidecar(tmp_path, "impl-ret-20260401-150000", "main")
        assert path.name == "base-branch"
        assert "impl-ret-20260401-150000" in str(path)

    def test_roundtrip_with_worktree_reader(self, tmp_path) -> None:
        """Write via write_worktree_sidecar, read via _read_sidecar_base_branch."""
        from autoskillit.execution.testing import _read_sidecar_base_branch

        name = "impl-rt-20260501-160000"
        branch = "develop"
        write_worktree_sidecar(tmp_path, name, branch)

        # Simulate worktree directory with .git file pointing to main repo
        wt_dir = tmp_path / name
        wt_dir.mkdir()
        main_git = tmp_path / ".git"
        main_git.mkdir()
        gitlink = wt_dir / ".git"
        gitlink.write_text(f"gitdir: {main_git}")

        read_branch = _read_sidecar_base_branch(wt_dir)
        assert read_branch == branch
