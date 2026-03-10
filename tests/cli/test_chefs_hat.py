"""Tests for the chefs-hat CLI command."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit import cli
from autoskillit.workspace.session_skills import DefaultSessionSkillManager


class TestChefsHat:
    # CH-1
    def test_chefs_hat_init_session_cook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """chefs-hat calls init_session with cook_session=True."""
        captured: dict = {}

        def fake_init_session(self, session_id: str, *, cook_session: bool = False) -> Path:
            captured["cook_session"] = cook_session
            return Path("/tmp/fake-skills")

        monkeypatch.setattr(DefaultSessionSkillManager, "init_session", fake_init_session)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
        cli.chefs_hat()
        assert captured["cook_session"] is True

    # CH-2
    def test_chefs_hat_launches_claude_add_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """chefs-hat passes --add-dir <skills_dir> to the subprocess."""
        captured_cmd: list = []
        fake_skills_dir = Path("/tmp/fake-skills-ch2")

        def fake_init_session(self, session_id: str, *, cook_session: bool = False) -> Path:
            return fake_skills_dir

        monkeypatch.setattr(DefaultSessionSkillManager, "init_session", fake_init_session)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: captured_cmd.extend(cmd))
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
        cli.chefs_hat()
        assert "--add-dir" in captured_cmd
        assert str(fake_skills_dir) in captured_cmd

    # CH-3
    def test_chef_alias_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """chef alias is registered in app.py alongside chefs-hat."""
        import importlib
        from pathlib import Path as _Path

        _app_mod = importlib.import_module("autoskillit.cli.app")
        src = _Path(_app_mod.__file__).read_text()
        assert 'name="chef"' in src or "name='chef'" in src, (
            "Expected 'chef' to be registered as a command name in app.py"
        )

    # CH-4
    def test_chefs_hat_sets_kitchen_open_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """chefs-hat sets AUTOSKILLIT_KITCHEN_OPEN=1 in the subprocess env."""
        captured_env: dict = {}

        def fake_init_session(self, session_id: str, *, cook_session: bool = False) -> Path:
            return Path("/tmp/fake-skills-ch4")

        def fake_subprocess_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))

        monkeypatch.setattr(DefaultSessionSkillManager, "init_session", fake_init_session)
        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
        cli.chefs_hat()
        assert captured_env.get("AUTOSKILLIT_KITCHEN_OPEN") == "1"

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
