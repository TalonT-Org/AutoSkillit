"""Tests: cook CLI order command — script validation, command building, env injection."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit import cli
from autoskillit.core import ClaudeFlags
from tests.cli.conftest import _SCRIPT_YAML

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class TestCLIOrderCommand:
    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub terminal preview to avoid subprocess.run collision with git calls."""
        monkeypatch.setattr(
            "autoskillit.cli._preview.show_cook_preview",
            lambda *a, **kw: None,
        )

    @pytest.fixture(autouse=True)
    def _interactive_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Most order() paths require an interactive TTY — default to True for this class."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    @pytest.fixture(autouse=True)
    def _stub_ingredients_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub _get_ingredients_table in app.py to prevent subprocess.run git calls."""
        import importlib
        import sys as _sys

        _app_mod = _sys.modules.get("autoskillit.cli._order") or importlib.import_module(
            "autoskillit.cli._order"
        )
        monkeypatch.setattr(_app_mod, "_get_ingredients_table", lambda *a, **kw: "| col | val |")

    def test_order_blocked_inside_claude_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 when CLAUDECODE env var is set."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDECODE", "1")
        with pytest.raises(SystemExit) as exc_info:
            cli.order("any-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "regular terminal" in captured.out.lower()

    def test_order_script_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 when script name doesn't match any entry."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)

        with pytest.raises(SystemExit) as exc_info:
            cli.order("nonexistent")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "nonexistent" in captured.out

    def test_order_no_scripts_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 with available bundled recipes listed when name not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            cli.order("anything")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Recipe not found: 'anything'" in captured.out
        assert "Available recipes:" in captured.out
        assert "implementation" in captured.out

    def test_order_available_scripts_listed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order lists available scripts when name doesn't match."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)

        with pytest.raises(SystemExit) as exc_info:
            cli.order("nonexistent")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Available recipes:" in captured.out
        assert "test-script" in captured.out

    def test_order_claude_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 when claude command is not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")

        with pytest.raises(SystemExit) as exc_info:
            cli.order("test-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "claude" in captured.out.lower()

    def test_order_invalid_script_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 when script YAML fails validation."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "bad-script.yaml").write_text("name: bad-script\nsteps: {}\n")

        with pytest.raises(SystemExit) as exc_info:
            cli.order("bad-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "validation" in captured.out.lower() or "error" in captured.out.lower()

    @patch("autoskillit.cli.subprocess.run")
    def test_order_builds_correct_command(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order passes correct flags to subprocess.run."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert ClaudeFlags.PLUGIN_DIR in cmd
        plugin_dir_idx = cmd.index(ClaudeFlags.PLUGIN_DIR)
        plugin_dir_val = Path(cmd[plugin_dir_idx + 1])
        assert plugin_dir_val.is_dir()
        assert (plugin_dir_val / ".claude-plugin" / "plugin.json").is_file()
        assert ClaudeFlags.TOOLS in cmd
        tools_idx = cmd.index(ClaudeFlags.TOOLS)
        assert cmd[tools_idx + 1] == "AskUserQuestion"
        assert ClaudeFlags.APPEND_SYSTEM_PROMPT in cmd
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in cmd
        assert ClaudeFlags.PRINT not in cmd
        assert ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS not in cmd
        kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
        assert "capture_output" not in kwargs
        assert "stdin" not in kwargs

    @patch("autoskillit.cli.subprocess.run")
    def test_order_suppresses_plugin_dir_when_plugin_installed(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order omits --plugin-dir when marketplace plugin is installed."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        from autoskillit.core._plugin_ids import MARKETPLACE_PREFIX

        monkeypatch.setattr(
            "autoskillit.core.detect_autoskillit_mcp_prefix",
            lambda: MARKETPLACE_PREFIX,
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert ClaudeFlags.PLUGIN_DIR not in cmd

    @patch("autoskillit.cli.subprocess.run")
    def test_order_includes_plugin_dir_when_no_plugin_installed(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order includes --plugin-dir when marketplace plugin is not installed."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        from autoskillit.core._plugin_ids import DIRECT_PREFIX

        monkeypatch.setattr(
            "autoskillit.core.detect_autoskillit_mcp_prefix", lambda: DIRECT_PREFIX
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert ClaudeFlags.PLUGIN_DIR in cmd

    @patch("autoskillit.cli.subprocess.run")
    def test_order_propagates_exit_code(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order does not raise SystemExit on returncode 0."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")  # should not raise
        mock_run.assert_called_once()

    @patch("autoskillit.cli.subprocess.run")
    def test_order_subprocess_failure_propagates(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order propagates non-zero subprocess exit codes."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=42, stdout="", stderr=""
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.order("test-script")
        assert exc_info.value.code == 42

    @patch("autoskillit.cli.subprocess.run")
    def test_order_uses_dangerously_skip_permissions(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order passes --dangerously-skip-permissions to claude."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        cmd = mock_run.call_args[0][0]
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in cmd

    def test_order_recipe_not_found_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """order exits 1 when the given recipe name is not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            cli.order("totally-unknown-recipe-xyz")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "totally-unknown-recipe-xyz" in captured.out

    def test_order_malformed_yaml_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """order exits 1 with YAML parse error message when load_recipe raises YAMLError."""
        from autoskillit.core import YAMLError

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=MagicMock()),
            patch("autoskillit.recipe.load_recipe", side_effect=YAMLError("bad yaml")),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli.order("bad-recipe")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "YAML parse error" in captured.out

    def test_order_structure_error_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """order exits 1 with structure error message when load_recipe raises ValueError."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=MagicMock()),
            patch("autoskillit.recipe.load_recipe", side_effect=ValueError("bad structure")),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli.order("bad-recipe")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "structure error" in captured.out

    @patch("autoskillit.cli.subprocess.run")
    def test_order_launch_sets_session_type_order(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order() passes AUTOSKILLIT_SESSION_TYPE=order to subprocess env."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("autoskillit.core.write_registry_entry"):
            cli.order("test-script")

        env = mock_run.call_args[1].get("env") or {}
        assert env.get("AUTOSKILLIT_SESSION_TYPE") == "order"

    @patch("autoskillit.cli.subprocess.run")
    def test_order_launch_sets_launch_id_env(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order() passes AUTOSKILLIT_LAUNCH_ID in subprocess env."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("autoskillit.core.write_registry_entry"):
            cli.order("test-script")

        env = mock_run.call_args[1].get("env") or {}
        assert "AUTOSKILLIT_LAUNCH_ID" in env


# SC-B-4: mark_onboarded() must NOT be called when the cook subprocess exits non-zero
def test_cook_mark_onboarded_not_called_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """mark_onboarded() must not be called when the cook subprocess exits non-zero."""
    import autoskillit.cli._cook as _cook

    onboarded_calls: list[Path] = []
    monkeypatch.setattr(
        "autoskillit.cli._onboarding.mark_onboarded",
        lambda project_dir: onboarded_calls.append(project_dir),
    )

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        return type("R", (), {"returncode": 1})()

    monkeypatch.setattr(_cook.subprocess, "run", fake_run)

    with pytest.raises(SystemExit):
        _cook._run_cook_session(
            cmd=["claude", "--test"],
            env={},
            _first_run=True,
            initial_prompt="test",
            project_dir=tmp_path,
        )

    assert onboarded_calls == [], (
        "mark_onboarded() must not be called when the subprocess exits non-zero"
    )
