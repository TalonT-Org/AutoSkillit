"""Tests for the chefs-hat CLI command."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from autoskillit import cli
from autoskillit.workspace.session_skills import DefaultSessionSkillManager


class TestChefsHat:
    # CH-1
    def test_chefs_hat_init_session_cook(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """chefs-hat calls init_session with cook_session=True."""
        captured: dict = {}
        fake_skills_dir = tmp_path / "fake-skills"
        fake_skills_dir.mkdir()

        def fake_init_session(
            self, session_id: str, *, cook_session: bool = False, config=None, project_dir=None
        ) -> Path:
            captured["cook_session"] = cook_session
            return fake_skills_dir

        monkeypatch.setattr(DefaultSessionSkillManager, "init_session", fake_init_session)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        cli.chefs_hat()
        assert captured["cook_session"] is True

    # CH-2
    def test_chefs_hat_launches_claude_add_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """chefs-hat passes --add-dir <skills_dir> to the subprocess."""
        captured_cmd: list = []
        fake_skills_dir = tmp_path / "fake-skills-ch2"
        fake_skills_dir.mkdir()

        def fake_init_session(
            self, session_id: str, *, cook_session: bool = False, config=None, project_dir=None
        ) -> Path:
            return fake_skills_dir

        def fake_run(cmd, **kw):
            captured_cmd.extend(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(DefaultSessionSkillManager, "init_session", fake_init_session)
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        cli.chefs_hat()
        assert "--add-dir" in captured_cmd
        assert str(fake_skills_dir) in captured_cmd

    # CH-3
    def test_chef_alias_invokes_chefs_hat(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """chef alias invokes the chefs-hat behavior via the CLI."""
        from autoskillit.cli.app import app

        captured_cmd: list = []
        fake_skills_dir = tmp_path / "fake-skills-ch3"
        fake_skills_dir.mkdir()

        def fake_init_session(
            self, session_id: str, *, cook_session: bool = False, config=None, project_dir=None
        ) -> Path:
            return fake_skills_dir

        def fake_run(cmd, **kw):
            captured_cmd.extend(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(DefaultSessionSkillManager, "init_session", fake_init_session)
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        with pytest.raises(SystemExit) as exc_info:
            app(["chef"])
        assert exc_info.value.code == 0
        assert "--add-dir" in captured_cmd

    # CH-5
    def test_chefs_hat_exits_when_claude_not_on_path(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """chefs-hat exits 1 with a message if claude is not on PATH."""
        monkeypatch.setattr(shutil, "which", lambda x: None)
        with pytest.raises(SystemExit) as exc_info:
            cli.chefs_hat()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "claude" in captured.out.lower() or "PATH" in captured.out

    # CH-6
    def test_chefs_hat_passes_plugin_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """chefs-hat subprocess call includes --plugin-dir <pkg_root()> (REQ-TIER-011)."""
        from unittest.mock import MagicMock, patch

        from autoskillit.core import pkg_root

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir

        # DefaultSessionSkillManager is imported inside the chefs_hat() function body,
        # so it must be patched via its source module (autoskillit.workspace), not via
        # the _chefs_hat module namespace.
        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            import autoskillit.cli._chefs_hat as module

            module.chefs_hat()

        args = mock_run.call_args[0][0]
        assert "--plugin-dir" in args
        idx = args.index("--plugin-dir")
        assert args[idx + 1] == str(pkg_root())
