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

        def fake_init_session(self, session_id: str, *, cook_session: bool = False) -> Path:
            captured["cook_session"] = cook_session
            return fake_skills_dir

        monkeypatch.setattr(DefaultSessionSkillManager, "init_session", fake_init_session)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
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

        def fake_init_session(self, session_id: str, *, cook_session: bool = False) -> Path:
            return fake_skills_dir

        def fake_run(cmd, **kw):
            captured_cmd.extend(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(DefaultSessionSkillManager, "init_session", fake_init_session)
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
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

        def fake_init_session(self, session_id: str, *, cook_session: bool = False) -> Path:
            return fake_skills_dir

        def fake_run(cmd, **kw):
            captured_cmd.extend(cmd)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(DefaultSessionSkillManager, "init_session", fake_init_session)
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/claude")
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
