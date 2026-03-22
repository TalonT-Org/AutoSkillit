"""Tests for the cook CLI command (interactive skill session)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from autoskillit import cli
from autoskillit.workspace.session_skills import DefaultSessionSkillManager


class TestCookInteractive:
    @pytest.fixture(autouse=True)
    def _no_first_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: False)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

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

    # CH-8
    def test_cook_calls_onboarding_menu_on_first_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When is_first_run() returns True, run_onboarding_menu is called once."""
        from unittest.mock import MagicMock, patch

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir
        menu_called: list[bool] = []

        # Override the autouse fixture's patch with True for this test
        monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: True)
        monkeypatch.setattr(
            "autoskillit.cli._onboarding.run_onboarding_menu",
            lambda *a, **kw: menu_called.append(True) or None,
        )

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            import autoskillit.cli._cook as module

            module.cook()

        assert menu_called == [True]

    # CH-9
    def test_cook_skips_onboarding_if_not_first_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When is_first_run() returns False, run_onboarding_menu is NOT called."""
        from unittest.mock import MagicMock, patch

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir
        menu_called: list[bool] = []

        monkeypatch.setattr(
            "autoskillit.cli._onboarding.run_onboarding_menu",
            lambda *a, **kw: menu_called.append(True) or None,
        )

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            import autoskillit.cli._cook as module

            module.cook()

        assert menu_called == []

    # CH-10
    def test_cook_forwards_initial_prompt_to_build_cmd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When run_onboarding_menu returns a non-None string, it appears in subprocess cmd."""
        from unittest.mock import MagicMock, patch

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir
        prompt_text = "/autoskillit:setup-project"

        monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: True)
        monkeypatch.setattr(
            "autoskillit.cli._onboarding.run_onboarding_menu",
            lambda *a, **kw: prompt_text,
        )

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            import autoskillit.cli._cook as module

            module.cook()

        args = mock_run.call_args[0][0]
        assert prompt_text in args

    # CH-11
    def test_cook_marks_onboarded_in_finally_when_prompt_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When run_onboarding_menu returns non-None and session completes, mark called."""
        from unittest.mock import MagicMock, patch

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir
        marked: list[bool] = []

        monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: True)
        monkeypatch.setattr(
            "autoskillit.cli._onboarding.run_onboarding_menu",
            lambda *a, **kw: "/autoskillit:setup-project",
        )
        monkeypatch.setattr(
            "autoskillit.cli._onboarding.mark_onboarded",
            lambda _p: marked.append(True),
        )

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            import autoskillit.cli._cook as module

            module.cook()

        assert marked == [True]

    # CH-12
    def test_cook_does_not_mark_onboarded_when_prompt_is_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When run_onboarding_menu returns None, mark_onboarded is NOT called from finally."""
        from unittest.mock import MagicMock, patch

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir
        marked: list[bool] = []

        monkeypatch.setattr("autoskillit.cli._onboarding.is_first_run", lambda _: True)
        monkeypatch.setattr(
            "autoskillit.cli._onboarding.run_onboarding_menu",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "autoskillit.cli._onboarding.mark_onboarded",
            lambda _p: marked.append(True),
        )

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            import autoskillit.cli._cook as module

            module.cook()

        assert marked == []
