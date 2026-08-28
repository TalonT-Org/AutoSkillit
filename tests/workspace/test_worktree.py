# tests/workspace/test_worktree.py

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import CleanupResult
from autoskillit.workspace import (
    create_git_worktree,
    list_git_worktrees,
    remove_git_worktree,
    remove_worktree_sidecar,
    write_worktree_sidecar,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


class TestCreateGitWorktree:
    """create_git_worktree validates ownership before and after Git mutates disk."""

    @pytest.mark.anyio
    async def test_uses_detached_add_command_and_returns_materialized_destination(self, tmp_path):
        project_root = tmp_path / "repo"
        worktree_root = tmp_path / "worktrees"
        destination = worktree_root / "assignment"
        project_root.mkdir()

        async def add_worktree(cmd, *, cwd, timeout):
            destination.mkdir(parents=True)
            return MagicMock(returncode=0, stdout="", stderr="")

        runner = AsyncMock(side_effect=add_worktree)

        result = await create_git_worktree(
            project_root, worktree_root, destination, "abc123", runner
        )

        assert result == destination
        runner.assert_awaited_once_with(
            [
                "git",
                "-C",
                str(project_root),
                "worktree",
                "add",
                "--detach",
                str(destination),
                "abc123",
            ],
            cwd=project_root,
            timeout=30,
        )

    @pytest.mark.anyio
    async def test_nonzero_add_raises_without_claiming_destination(self, tmp_path):
        project_root = tmp_path / "repo"
        destination = tmp_path / "worktrees" / "assignment"
        project_root.mkdir()
        runner = AsyncMock(return_value=MagicMock(returncode=1, stdout="", stderr="fatal: bad"))

        with pytest.raises(RuntimeError, match="Git worktree add failed"):
            await create_git_worktree(
                project_root, destination.parent, destination, "abc123", runner
            )

        assert not destination.exists()
        assert runner.await_count == 1

    @pytest.mark.anyio
    async def test_rejects_existing_and_symlinked_destinations_before_git_runs(self, tmp_path):
        project_root = tmp_path / "repo"
        worktree_root = tmp_path / "worktrees"
        project_root.mkdir()
        worktree_root.mkdir()
        runner = AsyncMock()

        existing = worktree_root / "existing"
        existing.mkdir()
        with pytest.raises(FileExistsError):
            await create_git_worktree(project_root, worktree_root, existing, "abc123", runner)

        symlinked = worktree_root / "symlinked"
        symlinked.symlink_to(tmp_path / "outside")
        with pytest.raises(FileExistsError):
            await create_git_worktree(project_root, worktree_root, symlinked, "abc123", runner)

        runner.assert_not_awaited()

    @pytest.mark.anyio
    async def test_rejects_destination_outside_trusted_root_before_git_runs(self, tmp_path):
        project_root = tmp_path / "repo"
        worktree_root = tmp_path / "worktrees"
        project_root.mkdir()
        runner = AsyncMock()

        with pytest.raises(ValueError, match="escapes trusted root"):
            await create_git_worktree(
                project_root,
                worktree_root,
                tmp_path / "worktrees-sibling" / "assignment",
                "abc123",
                runner,
            )

        runner.assert_not_awaited()

    @pytest.mark.anyio
    async def test_post_create_validation_rolls_back_and_reports_rollback_failure(self, tmp_path):
        project_root = tmp_path / "repo"
        worktree_root = tmp_path / "worktrees"
        destination = worktree_root / "assignment"
        project_root.mkdir()
        calls = 0

        async def runner(cmd, *, cwd, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("not a worktree")
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="not registered")

        with pytest.raises(RuntimeError, match="post-create validation failed.*rollback failed"):
            await create_git_worktree(project_root, worktree_root, destination, "abc123", runner)

        assert calls == 2


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

    def test_roundtrip_file_content(self, tmp_path) -> None:
        """Write via write_worktree_sidecar, verify file readable with stdlib."""
        name = "impl-rt-20260501-160000"
        branch = "develop"
        sidecar_path = write_worktree_sidecar(tmp_path, name, branch)

        assert sidecar_path.is_file()
        assert sidecar_path.read_text().strip() == branch
