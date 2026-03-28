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

    # REQ-EPH-001
    def test_cook_does_not_rmtree_skills_dir(self, monkeypatch, tmp_path):
        """shutil.rmtree must NOT be called on skills_dir after session exits."""
        from unittest.mock import MagicMock, patch

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir

        rmtree_calls = []
        original_rmtree = shutil.rmtree

        def tracking_rmtree(path, **kw):
            if Path(str(path)) == fake_skills_dir:
                rmtree_calls.append(path)
            else:
                original_rmtree(path, **kw)

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
            patch("shutil.rmtree", side_effect=tracking_rmtree),
        ):
            import autoskillit.cli._cook as module

            module.cook()

        assert not rmtree_calls, "cook() must not rmtree skills_dir on exit"

    # REQ-CLI-001 + REQ-CLI-002
    def test_cook_resume_passes_resume_flag(self, monkeypatch, tmp_path):
        """cook(resume=True) passes --resume <session_id> to subprocess."""
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
            patch(
                "autoskillit.core.find_latest_session_id",
                return_value="session123",
            ),
        ):
            import autoskillit.cli._cook as module

            module.cook(resume=True)

        args = mock_run.call_args[0][0]
        assert "--resume" in args
        idx = args.index("--resume")
        assert args[idx + 1] == "session123"

    # REQ-CLI-002
    def test_cook_resume_explicit_session_id(self, monkeypatch, tmp_path):
        """cook(resume=True, session_id='abc') uses the explicit id, skips discovery."""
        from unittest.mock import MagicMock, patch

        fake_skills_dir = tmp_path / "skills"
        fake_skills_dir.mkdir()
        mock_mgr = MagicMock()
        mock_mgr.init_session.return_value = fake_skills_dir
        discovery_calls = []

        def fake_discover(cwd=None):
            discovery_calls.append(cwd)
            return "should-not-be-used"

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("builtins.input", return_value=""),
            patch("autoskillit.workspace.DefaultSessionSkillManager", return_value=mock_mgr),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
            patch("autoskillit.core.find_latest_session_id", side_effect=fake_discover),
        ):
            import autoskillit.cli._cook as module

            module.cook(resume=True, session_id="explicit-abc")

        args = mock_run.call_args[0][0]
        assert "--resume" in args
        assert args[args.index("--resume") + 1] == "explicit-abc"
        assert not discovery_calls, "discovery must not be called when session_id is explicit"

    # REQ-CLI-002 — fallback when no session exists
    def test_cook_resume_falls_back_to_fresh_when_no_session(self, monkeypatch, tmp_path):
        """cook(resume=True) with no prior session starts a fresh session."""
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
            patch("autoskillit.core.find_latest_session_id", return_value=None),
        ):
            import autoskillit.cli._cook as module

            module.cook(resume=True)

        args = mock_run.call_args[0][0]
        assert "--resume" not in args

    # REQ-CLI-003
    def test_cook_cmd_resume_with_session_id_no_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cook --resume <uuid> must not raise UnusedCliTokensError — REQ-CLI-003."""
        import sys
        from unittest.mock import patch

        app_mod = sys.modules["autoskillit.cli.app"]

        captured: dict = {}

        def fake_cook(*, resume: bool = False, session_id: str | None = None) -> None:
            captured["resume"] = resume
            captured["session_id"] = session_id

        with patch.object(app_mod, "cook_interactive", fake_cook):
            # This MUST NOT raise UnusedCliTokensError (exit 0, not exit 1)
            with pytest.raises(SystemExit) as exc_info:
                app_mod.app(["cook", "--resume", "fa910a41-d1ca-4cae-b878-01028a0c7c1c"])
            assert exc_info.value.code == 0

        assert captured["session_id"] == "fa910a41-d1ca-4cae-b878-01028a0c7c1c"
        assert captured["resume"] is True

    # REQ-CLI-003
    def test_cook_cmd_resume_without_session_id_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """app(['cook', '--resume']) with no uuid must still work — REQ-CLI-003."""
        import sys
        from unittest.mock import patch

        app_mod = sys.modules["autoskillit.cli.app"]

        captured: dict = {}

        def fake_cook(*, resume: bool = False, session_id: str | None = None) -> None:
            captured["resume"] = resume
            captured["session_id"] = session_id

        with patch.object(app_mod, "cook_interactive", fake_cook):
            with pytest.raises(SystemExit) as exc_info:
                app_mod.app(["cook", "--resume"])
            assert exc_info.value.code == 0

        assert captured["resume"] is True
        assert captured["session_id"] is None

    # REQ-CLI-003
    def test_cook_cmd_positional_session_id_implies_resume(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """app(['cook', '<uuid>']) without --resume must imply resume=True — REQ-CLI-003."""
        import sys
        from unittest.mock import patch

        app_mod = sys.modules["autoskillit.cli.app"]

        captured: dict = {}

        def fake_cook(*, resume: bool = False, session_id: str | None = None) -> None:
            captured["resume"] = resume
            captured["session_id"] = session_id

        with patch.object(app_mod, "cook_interactive", fake_cook):
            with pytest.raises(SystemExit) as exc_info:
                app_mod.app(["cook", "fa910a41-d1ca-4cae-b878-01028a0c7c1c"])
            assert exc_info.value.code == 0

        assert captured["session_id"] == "fa910a41-d1ca-4cae-b878-01028a0c7c1c"
        assert captured["resume"] is True
