"""Tests for the cook CLI command (interactive skill session)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from autoskillit import cli
from autoskillit.workspace.session_skills import DefaultSessionSkillManager


class TestCookInteractive:
    # CH-1
    def test_cook_init_session_cook(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """cook calls init_session with cook_session=True."""
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
        cli.cook()
        assert captured["cook_session"] is True

    # CH-2
    def test_cook_launches_claude_add_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """cook passes --add-dir <skills_dir> to the subprocess."""
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
        cli.cook()
        assert "--add-dir" in captured_cmd
        assert str(fake_skills_dir) in captured_cmd

    # CH-3
    def test_cook_alias_c(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """'c' alias invokes the cook behavior via the CLI."""
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
            app(["c"])
        assert exc_info.value.code == 0
        assert "--add-dir" in captured_cmd

    # CH-5
    def test_cook_exits_when_claude_not_on_path(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 with a message if claude is not on PATH."""
        monkeypatch.setattr(shutil, "which", lambda x: None)
        with pytest.raises(SystemExit) as exc_info:
            cli.cook()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "claude" in captured.out.lower() or "PATH" in captured.out

    # CH-6
    def test_cook_passes_plugin_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """cook subprocess call includes --plugin-dir <pkg_root()> (REQ-TIER-011)."""
        from unittest.mock import MagicMock, patch

        from autoskillit.core import pkg_root

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            import autoskillit.cli._cook as module

            module.cook()

        mock_mgr.init_session.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "--plugin-dir" in args
        idx = args.index("--plugin-dir")
        assert args[idx + 1] == str(pkg_root())

    # CH-7
    def test_cook_includes_dangerously_skip_permissions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """cook subprocess cmd includes --dangerously-skip-permissions (REQ-TIER-012)."""
        from unittest.mock import MagicMock, patch

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            import autoskillit.cli._cook as module

            module.cook()

        args = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in args

    # T1: cook auto-opens kitchen
    def test_cook_launches_with_add_dir_and_system_prompt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """cook passes both --add-dir (for skills) and --append-system-prompt."""
        from unittest.mock import MagicMock, patch

        from autoskillit.core import ClaudeFlags

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            import autoskillit.cli._cook as module

            module.cook()

        args = mock_run.call_args[0][0]
        assert "--add-dir" in args
        assert ClaudeFlags.APPEND_SYSTEM_PROMPT in args

    # T2: cook auto-opens kitchen content
    def test_cook_auto_opens_kitchen(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """cook system prompt contains open_kitchen instruction."""
        from unittest.mock import MagicMock, patch

        from autoskillit.core import ClaudeFlags

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            import autoskillit.cli._cook as module

            module.cook()

        args = mock_run.call_args[0][0]
        prompt_idx = args.index(ClaudeFlags.APPEND_SYSTEM_PROMPT) + 1
        assert "open_kitchen" in args[prompt_idx]
