"""Tests for cli._workspace — age partitioning, display, and confirmation."""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from autoskillit.cli._workspace import _format_age, run_workspace_clean


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
    def test_no_runs_dir_prints_message_and_returns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # tmp_path has no autoskillit-runs/ subdirectory
        run_workspace_clean(dir=str(tmp_path))
        out = capsys.readouterr().out
        assert "No autoskillit-runs/" in out

    def test_all_entries_recent_prints_skipped_nothing_to_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()
        entry = runs_dir / "recent-run"
        entry.mkdir()
        entry_mtime = entry.stat().st_mtime
        # Monkeypatch time.time so the entry looks < 5h old (1 hour ago)
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 3600)

        run_workspace_clean(dir=str(tmp_path))
        out = capsys.readouterr().out
        assert "Skipped" in out
        assert "Nothing to clean" in out

    def test_stale_entries_force_true_deletes_without_prompt(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()
        entry = runs_dir / "stale-run"
        entry.mkdir()
        entry_mtime = entry.stat().st_mtime
        # 20 000 seconds > 5h threshold
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 20_000)

        run_workspace_clean(dir=str(tmp_path), force=True)
        out = capsys.readouterr().out
        assert "Removed:" in out
        assert "Cleaned 1" in out
        assert not entry.exists()

    def test_stale_entries_force_false_user_confirms_deletes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()
        entry = runs_dir / "stale-run"
        entry.mkdir()
        entry_mtime = entry.stat().st_mtime
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 20_000)
        monkeypatch.setattr(builtins, "input", lambda _: "y")

        run_workspace_clean(dir=str(tmp_path), force=False)
        assert not entry.exists()
        out = capsys.readouterr().out
        assert "Cleaned 1" in out

    def test_stale_entries_force_false_user_aborts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs_dir = tmp_path / "autoskillit-runs"
        runs_dir.mkdir()
        entry = runs_dir / "stale-run"
        entry.mkdir()
        entry_mtime = entry.stat().st_mtime
        monkeypatch.setattr("autoskillit.cli._workspace.time.time", lambda: entry_mtime + 20_000)
        monkeypatch.setattr(builtins, "input", lambda _: "n")

        run_workspace_clean(dir=str(tmp_path), force=False)
        assert entry.exists()
        out = capsys.readouterr().out
        assert "Aborted." in out

    def test_deletion_oserror_prints_to_stderr_and_counts(
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

        run_workspace_clean(dir=str(tmp_path), force=True)
        captured = capsys.readouterr()
        assert "permission denied" in captured.err
        assert "0 director" in captured.out
        assert "1 error(s)" in captured.out

    def test_mixed_stale_and_recent_only_stale_deleted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

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

        run_workspace_clean(dir=str(tmp_path), force=True)

        assert not stale_entry.exists()
        assert recent_entry.exists()

        out = capsys.readouterr().out
        assert "Skipped" in out
        assert "Cleaned 1" in out
