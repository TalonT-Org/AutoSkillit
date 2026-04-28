"""Tests: cook CLI workspace init and clean commands."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from autoskillit import cli
from autoskillit.cli._workspace import _format_age

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class TestCLIOrderWorkspace:
    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("autoskillit.cli._prompts.show_cook_preview", lambda *a, **kw: None)

    @pytest.fixture(autouse=True)
    def _interactive_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    @pytest.fixture(autouse=True)
    def _stub_ingredients_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import importlib
        import sys as _sys

        _app_mod = _sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        monkeypatch.setattr(_app_mod, "_get_ingredients_table", lambda *a, **kw: "| col | val |")

    def test_prep_station_init_creates_dir_with_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prep station init creates directory and drops marker file."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "test-workspace"
        cli.workspace_init(str(target))
        assert target.is_dir()
        assert (target / ".autoskillit-workspace").is_file()

    def test_prep_station_init_refuses_nonempty_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prep station init refuses to initialize a non-empty directory."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "existing"
        target.mkdir()
        (target / "important.txt").touch()
        with pytest.raises(SystemExit):
            cli.workspace_init(str(target))

    def test_prep_station_init_idempotent_on_empty_with_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prep station init is safe to re-run on a directory that only has the marker."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "prep-station"
        cli.workspace_init(str(target))
        cli.workspace_init(str(target))
        assert (target / ".autoskillit-workspace").is_file()

    def test_prep_station_init_marker_has_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marker file contains human-readable identifying content."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "prep-station"
        cli.workspace_init(str(target))
        content = (target / ".autoskillit-workspace").read_text()
        assert "autoskillit" in content
        assert "do not delete" in content

    def test_workspace_clean_removes_subdirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC1: workspace_clean removes stale subdirs of autoskillit-runs/."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "run-a").mkdir(parents=True)
        (runs_dir / "run-b").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        for d in (runs_dir / "run-a", runs_dir / "run-b"):
            os.utime(d, (old_time, old_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
        assert not (runs_dir / "run-a").exists()
        assert not (runs_dir / "run-b").exists()

    def test_workspace_clean_reports_nothing_when_no_runs_dir(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC2: workspace_clean prints message when autoskillit-runs/ doesn't exist."""
        cli.workspace_clean(dir=str(tmp_path))
        captured = capsys.readouterr()
        assert "No autoskillit-runs/" in captured.out

    def test_workspace_clean_defaults_to_parent_of_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T_WC3: workspace_clean without --dir uses parent of CWD."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "run-x").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "run-x", (old_time, old_time))
        cli.workspace_clean(force=True)
        assert not (runs_dir / "run-x").exists()

    def test_workspace_clean_recent_dirs_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC4: recent dirs (<5h) are not deleted, stale dirs (>5h) are."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "recent").mkdir(parents=True)
        (runs_dir / "stale").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "stale", (old_time, old_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
        assert (runs_dir / "recent").exists()
        assert not (runs_dir / "stale").exists()

    def test_workspace_clean_boundary_5h_is_stale(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC5: dir with mtime at the 5h threshold is stale (>=5h threshold)."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "boundary").mkdir(parents=True)
        boundary_time = time.time() - 5 * 3600 - 2
        os.utime(runs_dir / "boundary", (boundary_time, boundary_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
        assert not (runs_dir / "boundary").exists()

    def test_workspace_clean_skipped_dirs_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC6: skipped (recent) dirs are printed with age."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "recent-one").mkdir(parents=True)
        recent_time = time.time() - 3600
        os.utime(runs_dir / "recent-one", (recent_time, recent_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
        captured = capsys.readouterr()
        assert "Skipped" in captured.out
        assert "recent-one" in captured.out

    def test_workspace_clean_will_remove_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC7: stale dirs are printed as 'Will remove' with age."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "old-one").mkdir(parents=True)
        old_time = time.time() - 10 * 3600
        os.utime(runs_dir / "old-one", (old_time, old_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
        captured = capsys.readouterr()
        assert "Will remove" in captured.out
        assert "old-one" in captured.out

    def test_workspace_clean_confirm_defaults_no(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T_WC8: empty input at confirmation prompt defaults to N (no deletion)."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "stale").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "stale", (old_time, old_time))
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        cli.workspace_clean(dir=str(tmp_path))
        assert (runs_dir / "stale").exists()

    def test_workspace_clean_confirm_accepts_y(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T_WC9: 'y' at confirmation prompt deletes stale dirs."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "stale").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "stale", (old_time, old_time))
        monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
        cli.workspace_clean(dir=str(tmp_path))
        assert not (runs_dir / "stale").exists()

    def test_workspace_clean_force_skips_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T_WC10: --force deletes without calling input()."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "stale").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "stale", (old_time, old_time))
        monkeypatch.setattr(
            "builtins.input",
            lambda _prompt="": (_ for _ in ()).throw(AssertionError("input() called")),
        )
        cli.workspace_clean(dir=str(tmp_path), force=True)
        assert not (runs_dir / "stale").exists()

    def test_workspace_clean_no_stale_nothing_to_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC11: only recent dirs prints nothing-to-clean message."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "recent").mkdir(parents=True)
        cli.workspace_clean(dir=str(tmp_path), force=True)
        captured = capsys.readouterr()
        assert "Nothing to clean" in captured.out
        assert (runs_dir / "recent").exists()

    def test_format_age_various_values(self) -> None:
        """T_WC14: _format_age returns human-readable age strings."""
        assert _format_age(30 * 60) == "30m ago"
        assert _format_age(2 * 3600 + 14 * 60) == "2h 14m ago"
        assert _format_age(3 * 86400) == "3d ago"
        assert _format_age(5 * 3600) == "5h ago"

    def test_skills_list_shows_all_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """skills list outputs skill names with source labels."""
        monkeypatch.chdir(tmp_path)
        cli.skills_list()
        captured = capsys.readouterr()
        assert "investigate" in captured.out
        assert "bundled" in captured.out
        assert "NAME" in captured.out
