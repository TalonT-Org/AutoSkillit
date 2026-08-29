"""Tests for cli._workspace — age partitioning, display, and confirmation."""

from __future__ import annotations

import builtins
import os
from pathlib import Path

import pytest

from autoskillit.cli._workspace import _format_age, run_workspace_clean
from autoskillit.core import VANISHED_ERRORS
from autoskillit.workspace import CleanupResult
from tests._helpers import delete_once_then_delegate

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestFormatAge:
    def test_format_age_under_one_hour_returns_minutes(self) -> None:
        assert _format_age(90) == "1m ago"
        assert _format_age(3599) == "59m ago"
        assert _format_age(0) == "0m ago"

    def test_format_age_exact_hour_no_minutes(self) -> None:
        assert _format_age(3600) == "1h ago"
        assert _format_age(7200) == "2h ago"

    def test_format_age_hours_and_minutes(self) -> None:
        assert _format_age(3660) == "1h 1m ago"
        assert _format_age(5400) == "1h 30m ago"

    def test_format_age_one_day(self) -> None:
        assert _format_age(86400) == "1d ago"
        assert _format_age(172800) == "2d ago"

    def test_format_age_boundary_just_under_one_hour(self) -> None:
        result = _format_age(3599)
        assert "m ago" in result
        assert "h" not in result


class TestRunWorkspaceClean:
    @pytest.mark.anyio
    async def test_no_runs_dir_prints_message_and_returns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # tmp_path has no autoskillit-runs/ subdirectory
        await run_workspace_clean(dir=str(tmp_path))
        out = capsys.readouterr().out
        assert "No autoskillit-runs/" in out

    @pytest.mark.anyio
    async def test_all_entries_recent_prints_skipped_nothing_to_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()
        entry = runs_dir / "recent-run"
        entry.mkdir()
        entry_mtime = entry.stat().st_mtime
        # Monkeypatch time.time so the entry looks < 5h old (1 hour ago)
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 3600)

        await run_workspace_clean(dir=str(tmp_path))
        out = capsys.readouterr().out
        assert "Skipped" in out
        assert "Nothing to clean" in out

    @pytest.mark.anyio
    async def test_stale_entries_force_true_deletes_without_prompt(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()
        entry = runs_dir / "stale-run"
        entry.mkdir()
        entry_mtime = entry.stat().st_mtime
        # 20 000 seconds > 5h threshold
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 20_000)

        await run_workspace_clean(dir=str(tmp_path), force=True)
        out = capsys.readouterr().out
        assert "Removed:" in out
        assert "Cleaned 1" in out
        assert not entry.exists()

    @pytest.mark.anyio
    async def test_stale_entries_force_false_user_confirms_deletes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()
        entry = runs_dir / "stale-run"
        entry.mkdir()
        entry_mtime = entry.stat().st_mtime
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 20_000)
        monkeypatch.setattr(builtins, "input", lambda _: "y")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        await run_workspace_clean(dir=str(tmp_path), force=False)
        assert not entry.exists()
        out = capsys.readouterr().out
        assert "Cleaned 1" in out

    @pytest.mark.anyio
    async def test_stale_entries_force_false_user_aborts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()
        entry = runs_dir / "stale-run"
        entry.mkdir()
        entry_mtime = entry.stat().st_mtime
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 20_000)
        monkeypatch.setattr(builtins, "input", lambda _: "n")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        await run_workspace_clean(dir=str(tmp_path), force=False)
        assert entry.exists()
        out = capsys.readouterr().out
        assert "Aborted." in out

    @pytest.mark.anyio
    async def test_deletion_oserror_prints_to_stderr_and_counts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()
        entry = runs_dir / "stale-run"
        entry.mkdir()
        entry_mtime = entry.stat().st_mtime
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 20_000)
        monkeypatch.setattr(
            "autoskillit.cli._workspace.shutil.rmtree",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("permission denied")),
        )

        await run_workspace_clean(dir=str(tmp_path), force=True)
        captured = capsys.readouterr()
        assert "permission denied" in captured.err
        assert "0 director" in captured.out
        assert "1 error(s)" in captured.out

    @pytest.mark.anyio
    async def test_mixed_stale_and_recent_only_stale_deleted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()

        stale_entry = runs_dir / "stale-run"
        stale_entry.mkdir()
        recent_entry = runs_dir / "recent-run"
        recent_entry.mkdir()

        # Pin mtimes explicitly so fake_now can be chosen deterministically.
        # threshold = 5 * 3600 = 18_000 s
        # stale_mtime=1_000, recent_mtime=18_001, fake_now=21_000
        #   → stale age = 20_000 >= 18_000  (stale)
        #   → recent age =  2_999 <  18_000 (recent)
        stale_mtime = 1_000.0
        recent_mtime = 18_001.0
        fake_now = 21_000.0
        os.utime(stale_entry, (stale_mtime, stale_mtime))
        os.utime(recent_entry, (recent_mtime, recent_mtime))
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: fake_now)

        await run_workspace_clean(dir=str(tmp_path), force=True)

        assert not stale_entry.exists()
        assert recent_entry.exists()

        out = capsys.readouterr().out
        assert "Skipped" in out
        assert "Cleaned 1" in out


class TestRunWorkspaceCleanWorktrees:
    """workspace clean discovers and removes stale git worktrees."""

    @pytest.mark.anyio
    async def test_stale_worktree_is_removed_via_git(self, tmp_path, monkeypatch):
        """A stale worktree directory is removed with remove_git_worktree, not shutil.rmtree."""
        import time

        base = tmp_path
        wt_root = base / "worktrees"
        wt_root.mkdir()
        stale_wt = wt_root / "impl-foo-20260101-120000"
        stale_wt.mkdir()
        # Capture real time.time before patching to avoid recursion
        real_time = time.time
        mtime = real_time() - (6 * 3600)
        os.utime(stale_wt, (mtime, mtime))

        project_root = base / "myproject"
        project_root.mkdir()

        removed_paths = []

        async def fake_remove(path, main_repo, runner):
            removed_paths.append(path)
            return CleanupResult(deleted=[str(path)])

        async def fake_list(proj, prefix, runner):
            return [stale_wt]

        monkeypatch.setattr("autoskillit.cli._workspace.list_git_worktrees", fake_list)
        monkeypatch.setattr("autoskillit.cli._workspace.remove_git_worktree", fake_remove)
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", real_time)
        monkeypatch.setattr(
            "autoskillit.cli._workspace.load_config", lambda p=None: _make_workspace_cfg()
        )

        await run_workspace_clean(dir=str(base), force=True, project_root=project_root)
        assert stale_wt in removed_paths

    @pytest.mark.anyio
    async def test_recent_worktree_is_skipped(self, tmp_path, monkeypatch):
        """A worktree modified within 5h is not removed."""
        import time

        base = tmp_path
        wt_root = base / "worktrees"
        wt_root.mkdir()
        recent_wt = wt_root / "impl-bar-20260101-120000"
        recent_wt.mkdir()

        project_root = base / "myproject"
        project_root.mkdir()

        removed_paths = []

        async def fake_remove(path, main_repo, runner):
            removed_paths.append(path)
            return CleanupResult(deleted=[str(path)])

        async def fake_list(proj, prefix, runner):
            return [recent_wt]

        real_time = time.time
        monkeypatch.setattr("autoskillit.cli._workspace.list_git_worktrees", fake_list)
        monkeypatch.setattr("autoskillit.cli._workspace.remove_git_worktree", fake_remove)
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", real_time)
        monkeypatch.setattr(
            "autoskillit.cli._workspace.load_config", lambda p=None: _make_workspace_cfg()
        )

        await run_workspace_clean(dir=str(base), force=True, project_root=project_root)
        assert len(removed_paths) == 0
        assert recent_wt not in removed_paths

    @pytest.mark.anyio
    async def test_orphaned_worktree_dir_removed(self, tmp_path, monkeypatch):
        """A stale directory under worktrees/ not registered in git is still removed."""
        import time

        base = tmp_path
        wt_root = base / "worktrees"
        wt_root.mkdir()
        orphan = wt_root / "impl-orphan-20260101-120000"
        orphan.mkdir()
        mtime = time.time() - (6 * 3600)
        os.utime(orphan, (mtime, mtime))

        project_root = base / "myproject"
        project_root.mkdir()

        # git reports no worktrees — orphan is filesystem-only
        async def fake_list(proj, prefix, runner):
            return []

        removed_paths = []

        async def fake_remove(path, main_repo, runner):
            removed_paths.append(path)
            return CleanupResult(deleted=[str(path)])

        monkeypatch.setattr("autoskillit.cli._workspace.list_git_worktrees", fake_list)
        monkeypatch.setattr("autoskillit.cli._workspace.remove_git_worktree", fake_remove)
        monkeypatch.setattr(
            "autoskillit.cli._workspace.load_config", lambda p=None: _make_workspace_cfg()
        )

        await run_workspace_clean(dir=str(base), force=True, project_root=project_root)
        assert orphan in removed_paths

    @pytest.mark.anyio
    async def test_workspace_clean_prints_the_correct_age_per_worktree(
        self, tmp_path, capsys, monkeypatch
    ):
        base = tmp_path
        worktrees_root = base / "worktrees"
        worktrees_root.mkdir()
        now = 100_000.0
        worktrees = {
            "impl-recent-minutes": (now - 60, "1m ago"),
            "impl-recent-hours": (now - 3660, "1h 1m ago"),
            "impl-stale-days": (now - 86400, "1d ago"),
        }
        paths: list[Path] = []
        for name, (mtime, _) in worktrees.items():
            path = worktrees_root / name
            path.mkdir()
            os.utime(path, (mtime, mtime))
            paths.append(path)

        async def fake_list(proj, prefix, runner):
            return paths

        async def fake_remove(path, main_repo, runner):
            return CleanupResult(deleted=[str(path)])

        monkeypatch.setattr("autoskillit.cli._workspace.list_git_worktrees", fake_list)
        monkeypatch.setattr("autoskillit.cli._workspace.remove_git_worktree", fake_remove)
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: now)
        monkeypatch.setattr(
            "autoskillit.cli._workspace.load_config", lambda p=None: _make_workspace_cfg()
        )

        await run_workspace_clean(dir=str(base), force=True, project_root=base)

        out = capsys.readouterr().out
        for name, (_, age) in worktrees.items():
            assert f"  {name}  ({age})" in out

    @pytest.mark.parametrize("vanished_error", VANISHED_ERRORS)
    @pytest.mark.anyio
    async def test_workspace_clean_handles_a_worktrees_root_replaced_by_a_file(
        self, tmp_path, capsys, monkeypatch, vanished_error
    ):
        base = tmp_path
        worktrees_root = base / "worktrees"
        worktrees_root.mkdir()

        async def fake_list(proj, prefix, runner):
            worktrees_root.rmdir()
            if vanished_error is NotADirectoryError:
                worktrees_root.write_text("not a directory", encoding="utf-8")
            return []

        monkeypatch.setattr("autoskillit.cli._workspace.list_git_worktrees", fake_list)
        monkeypatch.setattr(
            "autoskillit.cli._workspace.load_config", lambda p=None: _make_workspace_cfg()
        )

        await run_workspace_clean(dir=str(base), force=True, project_root=base)

        assert f"Nothing to clean in {worktrees_root}" in capsys.readouterr().out

    @pytest.mark.anyio
    async def test_workspace_clean_ignores_filesystem_only_symlink_entries(
        self, tmp_path, capsys, monkeypatch
    ):
        base = tmp_path
        worktrees_root = base / "worktrees"
        worktrees_root.mkdir()
        target = base / "target"
        target.mkdir()
        linked_worktree = worktrees_root / "linked-worktree"
        linked_worktree.symlink_to(target, target_is_directory=True)

        async def fake_list(proj, prefix, runner):
            return []

        monkeypatch.setattr("autoskillit.cli._workspace.list_git_worktrees", fake_list)
        monkeypatch.setattr(
            "autoskillit.cli._workspace.load_config", lambda p=None: _make_workspace_cfg()
        )

        await run_workspace_clean(dir=str(base), force=True, project_root=base)

        out = capsys.readouterr().out
        assert linked_worktree.name not in out
        assert f"Nothing to clean in {worktrees_root}" in out

    @pytest.mark.anyio
    async def test_sidecar_removed_alongside_worktree(self, tmp_path, monkeypatch):
        """When a worktree is removed, its sidecar is also removed."""
        import time

        base = tmp_path
        wt_root = base / "worktrees"
        wt_root.mkdir()
        stale_wt = wt_root / "impl-foo-20260101-120000"
        stale_wt.mkdir()
        os.utime(stale_wt, (time.time() - 6 * 3600,) * 2)

        project_root = base / "myproject"
        project_root.mkdir()

        sidecar_removed = []

        def fake_sidecar(proj_root, wt_name):
            sidecar_removed.append(wt_name)
            return CleanupResult(deleted=["sidecar"])

        async def fake_list(proj, prefix, runner):
            return [stale_wt]

        async def fake_remove(path, main_repo, runner):
            return CleanupResult(deleted=[str(path)])

        monkeypatch.setattr("autoskillit.cli._workspace.list_git_worktrees", fake_list)
        monkeypatch.setattr("autoskillit.cli._workspace.remove_git_worktree", fake_remove)
        monkeypatch.setattr("autoskillit.cli._workspace.remove_worktree_sidecar", fake_sidecar)
        monkeypatch.setattr(
            "autoskillit.cli._workspace.load_config", lambda p=None: _make_workspace_cfg()
        )

        await run_workspace_clean(dir=str(base), force=True, project_root=project_root)
        assert "impl-foo-20260101-120000" in sidecar_removed

    @pytest.mark.medium
    @pytest.mark.anyio
    async def test_worktree_deleted_between_listing_and_mtime_check_is_skipped(
        self, tmp_path, monkeypatch
    ):
        """A worktree deleted between list_git_worktrees and the mtime check must be
        skipped rather than crash. Regression guard for the `or 0.0` pitfall this
        migration must avoid: a vanished path's mtime must stay None (skipped by
        the `if mtime is None: continue` filter), not silently become 0.0 — which
        would route it into stale_wts and crash at the unguarded wt.stat() calls
        in the display loops below."""
        import shutil

        import autoskillit.cli._workspace as workspace_module

        base = tmp_path
        wt_root = base / "worktrees"
        wt_root.mkdir()
        vanishing_wt = wt_root / "impl-vanishing-20260101-120000"
        vanishing_wt.mkdir()

        project_root = base / "myproject"
        project_root.mkdir()

        async def fake_list(proj, prefix, runner):
            return [vanishing_wt]

        async def fake_remove(path, main_repo, runner):
            raise AssertionError("a vanished worktree must never reach removal")

        real_safe_mtime = workspace_module.safe_mtime
        stat_attempted = False

        def is_vanishing_worktree(p):
            nonlocal stat_attempted
            stat_attempted = True
            return p == vanishing_wt

        monkeypatch.setattr(workspace_module, "list_git_worktrees", fake_list)
        monkeypatch.setattr(workspace_module, "remove_git_worktree", fake_remove)
        monkeypatch.setattr(
            workspace_module,
            "safe_mtime",
            delete_once_then_delegate(
                real_safe_mtime,
                delete=lambda: shutil.rmtree(vanishing_wt),
                when=is_vanishing_worktree,
            ),
        )
        monkeypatch.setattr(workspace_module, "load_config", lambda p=None: _make_workspace_cfg())

        await run_workspace_clean(dir=str(base), force=True, project_root=project_root)

        assert stat_attempted

    @pytest.mark.parametrize(
        ("age", "is_stale"),
        ((60, False), (20_000, True)),
        ids=("recent", "stale"),
    )
    @pytest.mark.anyio
    async def test_workspace_clean_survives_a_worktree_vanishing_before_its_age_read(
        self, tmp_path, monkeypatch, age, is_stale
    ):
        import shutil

        import autoskillit.cli._workspace as workspace_module

        base = tmp_path
        worktrees_root = base / "worktrees"
        worktrees_root.mkdir()
        worktree = worktrees_root / "impl-vanishing-20260101-120000"
        worktree.mkdir()
        now = 100_000.0
        os.utime(worktree, (now - age, now - age))
        removed_paths: list[Path] = []

        async def fake_list(proj, prefix, runner):
            return [worktree]

        async def fake_remove(path, main_repo, runner):
            removed_paths.append(path)
            return CleanupResult(deleted=[str(path)])

        real_safe_mtime = workspace_module.safe_mtime

        def observe_then_remove(path: Path) -> float | None:
            mtime = real_safe_mtime(path)
            if path == worktree and worktree.exists():
                shutil.rmtree(worktree)
            return mtime

        monkeypatch.setattr(workspace_module, "list_git_worktrees", fake_list)
        monkeypatch.setattr(workspace_module, "remove_git_worktree", fake_remove)
        monkeypatch.setattr(workspace_module, "safe_mtime", observe_then_remove)
        monkeypatch.setattr(workspace_module.time, "time", lambda: now)
        monkeypatch.setattr(workspace_module, "load_config", lambda p=None: _make_workspace_cfg())

        await run_workspace_clean(dir=str(base), force=True, project_root=base)

        assert not worktree.exists()
        assert removed_paths == ([worktree] if is_stale else [])

    @pytest.mark.anyio
    async def test_workspace_clean_calls_load_config(self, tmp_path, monkeypatch):
        """run_workspace_clean calls load_config to resolve workspace paths."""
        load_called = []
        monkeypatch.setattr(
            "autoskillit.cli._workspace.load_config",
            lambda p=None: (load_called.append(p), _make_workspace_cfg())[1],
        )
        # Supply an empty base dir to trigger early return (no dirs to clean)
        await run_workspace_clean(dir=str(tmp_path), force=True, project_root=tmp_path)
        assert len(load_called) == 1


def _make_workspace_cfg():
    """Helper: minimal AutomationConfig with default WorkspaceConfig."""
    from autoskillit.config.settings import AutomationConfig

    return AutomationConfig()
